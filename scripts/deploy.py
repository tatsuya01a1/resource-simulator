"""Deploy only a clean, already-published work-branch commit. Never echo secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(ROOT / 'scripts'))
from azure_setup import az


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main():
    if git('branch', '--show-current') != 'codex/stage1-local-worker':
        raise RuntimeError('Deploy only the approved work branch')
    if git('status', '--porcelain'):
        raise RuntimeError('Commit and publish all changes before deployment')
    commit = git('rev-parse', 'HEAD')
    if commit != git('rev-parse', 'origin/codex/stage1-local-worker'):
        raise RuntimeError('Local commit must match the published work branch')
    secrets = ROOT / '.secrets'
    manifest = json.loads((secrets / 'deployment.json').read_text())
    settings_file = secrets / 'api-settings.json'
    settings = json.loads(settings_file.read_text())
    settings['properties']['BUILD_COMMIT'] = commit
    settings_file.write_text(json.dumps(settings))
    az('rest', '--method', 'put', '--url', 'https://management.azure.com' + manifest['site_id'] +
       '/config/appsettings?api-version=2023-12-01', '--body', '@' + str(settings_file))
    subprocess.run(['npm.cmd', '--prefix', 'web', 'run', 'build'], cwd=ROOT, check=True)
    # Only API source and lock enter the deployment; no local venv or credentials.
    stage = ROOT / '.cache' / 'api-deploy' / commit
    stage.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / 'api').rglob('*'):
        relative = path.relative_to(ROOT / 'api')
        if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
            continue
        if path.is_file() and (path.suffix == '.py' or path.name in ('host.json', 'requirements.txt', 'function.json')):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    # SWA CLI skips API builds. Package Linux CPython 3.11 wheels explicitly;
    # never upload this Windows machine's venv/site-packages.
    subprocess.run(['uv', 'pip', 'install', '--target', str(stage / '.python_packages/lib/site-packages'),
                    '--python-version', '3.11', '--python-platform', 'x86_64-manylinux_2_17',
                    '--only-binary', ':all:', '-r', 'api/requirements.txt'], cwd=ROOT, check=True)
    env = os.environ.copy()
    env['SWA_CLI_DEPLOYMENT_TOKEN'] = (secrets / 'swa-token.txt').read_text().strip()
    env['SWA_CLI_TELEMETRY_OPTOUT'] = '1'
    cli = ROOT / '.cache/deploy-tools/node_modules/@azure/static-web-apps-cli/dist/cli/bin.js'
    result = subprocess.run(['node', str(cli), 'deploy', 'web/dist', '--api-location', str(stage),
                             '--api-language', 'python', '--api-version', '3.11', '--env', 'production'],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
    # The CLI might log configuration. Redact the token even on failures.
    token = env['SWA_CLI_DEPLOYMENT_TOKEN']
    output = (result.stdout + result.stderr).replace(token, '[REDACTED]')
    print(output[-5000:])
    if result.returncode:
        raise RuntimeError('SWA deployment failed')
    manifest['deployed_commit'] = commit
    (secrets / 'deployment.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'url': manifest['url'], 'commit': commit, 'branch': manifest['source_branch']}))


if __name__ == '__main__':
    main()
