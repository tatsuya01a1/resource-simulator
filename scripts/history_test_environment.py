"""Isolated Azure rehearsal environment. Never reads production secrets/settings.

setup provisions test-only resources/identity; deploy requires a clean pushed
codex/ohlc-history commit. All credentials remain under .secrets/history-test.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

from azure.core.credentials import AzureNamedKeyCredential
from azure.data.tables import generate_table_sas, TableSasPermissions
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.secrets/history-test'
GROUP = 'rg-resource-simulator-history-test'
STORAGE = 'rsimhist20261024'
SITE = 'resource-simulator-history-test'
BRANCH = 'codex/ohlc-history'


def az(*args):
    result = subprocess.run(['az.cmd', *args, '--only-show-errors', '-o', 'json'],
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        # Never print HTTP bodies, settings, credentials or command arguments.
        raise RuntimeError('Azure test operation failed: ' + ' '.join(args[:2]))
    return json.loads(result.stdout) if result.stdout.strip() else None


def guard(manifest):
    if (manifest['group'] != GROUP or manifest['storage'] != STORAGE or manifest['site'] != SITE
            or f'/resourceGroups/{GROUP}/' not in manifest['site_id']):
        raise ValueError('isolated environment identity mismatch')


def setup():
    PRIVATE.mkdir(parents=True, exist_ok=True)
    owner = subprocess.check_output(['whoami'], text=True).strip()
    subprocess.run(['icacls', str(PRIVATE), '/inheritance:r', '/grant:r', owner + ':(OI)(CI)F'], check=True, capture_output=True)
    account = az('account', 'show')
    existing = [g for g in az('group', 'list') if g['name'] == GROUP]
    if existing and existing[0].get('tags', {}).get('purpose') != 'history-validation':
        raise ValueError('unowned test group')
    az('group', 'create', '--subscription', account['id'], '--name', GROUP, '--location', 'japaneast',
       '--tags', 'project=resource-simulator', 'purpose=history-validation')
    deployment = az('deployment', 'group', 'create', '--subscription', account['id'], '--resource-group', GROUP,
                    '--name', 'history-validation', '--template-file', str(ROOT / 'infra/history-test.bicep'),
                    '--parameters', 'storageName=' + STORAGE, 'siteName=' + SITE)
    outputs = {k: v['value'] for k, v in deployment['properties']['outputs'].items()}
    identity_file = PRIVATE / 'identity.json'
    if identity_file.exists():
        identity = json.loads(identity_file.read_text())
    else:
        app = az('ad', 'app', 'create', '--display-name', 'resource-simulator-history-validation')
        sp = az('ad', 'sp', 'create', '--id', app['appId'])
        identity = dict(client_id=app['appId'], object_id=sp['id'], tenant_id=account['tenantId'])
        identity_file.write_text(json.dumps(identity))
    private, public = PRIVATE / 'worker.pem', PRIVATE / 'worker-public.pem'
    now = datetime.now(timezone.utc)
    if not private.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'history-validation-only')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=30)).sign(key, hashes.SHA256()))
        public.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()) + public.read_bytes())
    if not az('ad', 'app', 'credential', 'list', '--id', identity['client_id']):
        az('ad', 'app', 'credential', 'reset', '--id', identity['client_id'], '--append',
           '--cert', '@' + str(public), '--display-name', 'history-validation')
    az('role', 'assignment', 'create', '--assignee-object-id', identity['object_id'],
       '--assignee-principal-type', 'ServicePrincipal', '--role', 'Storage Table Data Contributor',
       '--scope', outputs['tableScope'])
    cloud = dict(endpoint=outputs['tableEndpoint'], table='CurrentState', partition='resource-simulator',
                 tenant_id=identity['tenant_id'], client_id=identity['client_id'], certificate_path=str(private))
    (PRIVATE / 'cloud.toml').write_text('[cloud]\n' + '\n'.join(f'{k} = {json.dumps(v)}' for k, v in cloud.items()), encoding='utf-8')
    keys = az('storage', 'account', 'keys', 'list', '--subscription', account['id'], '--resource-group', GROUP, '--account-name', STORAGE)
    credential = AzureNamedKeyCredential(STORAGE, keys[0]['value'])
    settings = dict(TABLE_ENDPOINT=outputs['tableEndpoint'], TABLE_NAME='CurrentState')
    for name, lower, upper, single in [('TABLE_READ_SAS', 'resource-simulator', 'resource-simulator', True),
                                       ('HISTORY_READ_SAS', 'ohlc|', 'ohlc|~', False)]:
        kwargs = dict(start_rk='current', end_rk='current') if single else {}
        settings[name] = generate_table_sas(credential, 'CurrentState', permission=TableSasPermissions(read=True),
            start=now - timedelta(minutes=5), expiry=now + timedelta(days=7), start_pk=lower, end_pk=upper,
            protocol='https', **kwargs)
    (PRIVATE / 'api-settings.json').write_text(json.dumps(dict(properties=settings)))
    # Only the isolated bulk data loader uses this key, never the Worker or API.
    (PRIVATE / 'loader-key.txt').write_text(keys[0]['value'])
    token = az('staticwebapp', 'secrets', 'list', '--subscription', account['id'], '--resource-group', GROUP, '--name', SITE)
    (PRIVATE / 'swa-token.txt').write_text(token['properties']['apiKey'])
    manifest = dict(subscription=account['id'], group=GROUP, storage=STORAGE, site=SITE,
                    site_id=outputs['siteId'], url='https://' + outputs['hostname'],
                    table_endpoint=outputs['tableEndpoint'], created_at=now.isoformat())
    guard(manifest)
    (PRIVATE / 'deployment.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


def deploy():
    git = lambda *args: subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    if git('branch', '--show-current') != BRANCH or git('status', '--porcelain'):
        raise ValueError('deploy only clean history branch')
    commit = git('rev-parse', 'HEAD')
    if git('rev-parse', 'origin/' + BRANCH) != commit:
        raise ValueError('push/fetch history branch before deployment')
    manifest = json.loads((PRIVATE / 'deployment.json').read_text()); guard(manifest)
    settings = json.loads((PRIVATE / 'api-settings.json').read_text())
    if not settings['properties']['TABLE_ENDPOINT'].startswith('https://' + STORAGE + '.'):
        raise ValueError('test API must use test storage')
    settings['properties']['BUILD_COMMIT'] = commit
    (PRIVATE / 'api-settings.json').write_text(json.dumps(settings))
    az('rest', '--method', 'put', '--url', 'https://management.azure.com' + manifest['site_id'] +
       '/config/appsettings?api-version=2023-12-01', '--body', '@' + str(PRIVATE / 'api-settings.json'))
    subprocess.run(['npm.cmd', '--prefix', 'web', 'run', 'build'], cwd=ROOT, check=True)
    stage = ROOT / '.cache/history-api' / commit
    stage.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / 'api').rglob('*'):
        relative = path.relative_to(ROOT / 'api')
        if any(p.startswith('.') or p in ('tests', '__pycache__') for p in relative.parts):
            continue
        if path.is_file() and (path.suffix == '.py' or path.name in ('function.json', 'host.json', 'requirements.txt')):
            target = stage / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(path.read_bytes())
    subprocess.run(['uv', 'pip', 'install', '--target', str(stage / '.python_packages/lib/site-packages'),
                    '--python-version', '3.11', '--python-platform', 'x86_64-manylinux_2_17', '--only-binary', ':all:',
                    '-r', 'api/requirements.txt'], cwd=ROOT, check=True)
    env = os.environ.copy()
    env['SWA_CLI_DEPLOYMENT_TOKEN'] = (PRIVATE / 'swa-token.txt').read_text().strip()
    env['SWA_CLI_TELEMETRY_OPTOUT'] = '1'
    result = subprocess.run(['node', str(ROOT / '.cache/deploy-tools/node_modules/@azure/static-web-apps-cli/dist/cli/bin.js'),
                             'deploy', 'web/dist', '--api-location', str(stage), '--api-language', 'python',
                             '--api-version', '3.11', '--env', 'production', '--swa-config-location', str(ROOT / 'web/dist')], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('isolated SWA deploy failed')
    manifest['deployed_commit'] = commit
    (PRIVATE / 'deployment.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(dict(url=manifest['url'], commit=commit, test_only=True)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['setup', 'deploy'])
    args = parser.parse_args()
    setup() if args.action == 'setup' else deploy()
