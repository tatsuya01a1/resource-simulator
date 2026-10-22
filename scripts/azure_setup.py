"""Provision only this project's new RG. Developer CLI login is never used by Worker.

Run via the pinned project Python. Secrets stay in .secrets/, never stdout.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

from azure.data.tables import generate_table_sas, TableSasPermissions
from azure.core.credentials import AzureNamedKeyCredential
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / '.secrets'


def az(*args):
    result = subprocess.run(['az.cmd', *args, '--only-show-errors', '-o', 'json'],
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        # Avoid echoing commands/app settings/SAS in diagnostics.
        raise RuntimeError('Azure command failed: ' + ' '.join(args[:2]) + '\n' + result.stderr[:1000])
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--subscription', required=True)
    parser.add_argument('--group', default='rg-resource-simulator')
    parser.add_argument('--storage', required=True)
    args = parser.parse_args()
    account = az('account', 'show', '--subscription', args.subscription)
    groups = az('group', 'list', '--subscription', args.subscription)
    existing = next((g for g in groups if g['name'] == args.group), None)
    if existing and existing.get('tags', {}).get('project') != 'resource-simulator':
        raise RuntimeError('Refusing to modify a group owned by another project')
    SECRETS.mkdir(exist_ok=True)
    if os.name == 'nt':
        owner = subprocess.check_output(['whoami'], text=True).strip()
        subprocess.run(['icacls', str(SECRETS), '/inheritance:r', '/grant:r', owner + ':(OI)(CI)F'],
                       check=True, capture_output=True)
    az('group', 'create', '--subscription', args.subscription, '--name', args.group,
       '--location', 'japaneast', '--tags', 'project=resource-simulator')
    deployment = az('deployment', 'group', 'create', '--subscription', args.subscription,
                    '--resource-group', args.group, '--name', 'latest-state',
                    '--template-file', str(ROOT / 'infra' / 'main.bicep'), '--parameters',
                    'storageName=' + args.storage)
    outputs = {key: value['value'] for key, value in deployment['properties']['outputs'].items()}
    app_file = SECRETS / 'worker-app.json'
    if app_file.exists():
        identity = json.loads(app_file.read_text())
    else:
        app = az('ad', 'app', 'create', '--display-name', 'resource-simulator-worker')
        sp = az('ad', 'sp', 'create', '--id', app['appId'])
        identity = {'client_id': app['appId'], 'object_id': sp['id'], 'tenant_id': account['tenantId']}
        app_file.write_text(json.dumps(identity), encoding='utf-8')
    private = SECRETS / 'worker.pem'
    public = SECRETS / 'worker-public.pem'
    if not private.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'resource-simulator-worker')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=365)).sign(key, hashes.SHA256()))
        public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()) + public.read_bytes())
    credentials = az('ad', 'app', 'credential', 'list', '--id', identity['client_id'])
    if not any(c.get('displayName') == 'worker-certificate' for c in credentials):
        az('ad', 'app', 'credential', 'reset', '--id', identity['client_id'], '--append',
           '--cert', '@' + str(public), '--display-name', 'worker-certificate')
    az('role', 'assignment', 'create', '--subscription', args.subscription,
       '--assignee-object-id', identity['object_id'], '--assignee-principal-type', 'ServicePrincipal',
       '--role', 'Storage Table Data Contributor', '--scope', outputs['tableScope'])
    cloud = {'endpoint': outputs['tableEndpoint'], 'table': 'CurrentState',
             'partition': 'resource-simulator', **{k: identity[k] for k in ('tenant_id', 'client_id')},
             'certificate_path': str(private)}
    (SECRETS / 'cloud.toml').write_text('[cloud]\n' + '\n'.join(f'{k} = {json.dumps(v)}' for k, v in cloud.items()), encoding='utf-8')
    keys = az('storage', 'account', 'keys', 'list', '--subscription', args.subscription,
              '--resource-group', args.group, '--account-name', args.storage)
    sas = generate_table_sas(AzureNamedKeyCredential(args.storage, keys[0]['value']), 'CurrentState',
                            permission=TableSasPermissions(read=True),
                            expiry=datetime.now(timezone.utc) + timedelta(days=90),
                            start=datetime.now(timezone.utc) - timedelta(minutes=5),
                            start_pk='resource-simulator', end_pk='resource-simulator',
                            start_rk='current', end_rk='current', protocol='https')
    settings = {'TABLE_ENDPOINT': outputs['tableEndpoint'], 'TABLE_NAME': 'CurrentState', 'TABLE_READ_SAS': sas}
    settings_file = SECRETS / 'api-settings.json'
    settings_file.write_text(json.dumps({'properties': settings}), encoding='utf-8')
    az('rest', '--method', 'put', '--url', 'https://management.azure.com' + outputs['siteId'] +
       '/config/appsettings?api-version=2023-12-01', '--body', '@' + str(settings_file))
    deployment_token = az('staticwebapp', 'secrets', 'list', '--subscription', args.subscription,
                           '--resource-group', args.group, '--name', 'resource-simulator-web')
    (SECRETS / 'swa-token.txt').write_text(deployment_token['properties']['apiKey'], encoding='utf-8')
    manifest = {'subscription': args.subscription, 'group': args.group, 'storage': args.storage,
                'site': 'resource-simulator-web', 'url': 'https://' + outputs['hostname'],
                'source_branch': 'codex/stage1-local-worker', 'table_endpoint': outputs['tableEndpoint'],
                'site_id': outputs['siteId']}
    (SECRETS / 'deployment.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
