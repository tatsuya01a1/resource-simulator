"""Prepare restricted read SAS locally; does not apply settings or deploy.

Run only during an explicitly authorized production cutover. Existing current SAS
remains restricted to its single entity. Never print a key, SAS, or settings body.
"""
from datetime import datetime, timedelta, timezone
import json
from azure.core.credentials import AzureNamedKeyCredential
from azure.data.tables import generate_table_sas, TableSasPermissions
from azure_setup import az, SECRETS


def main():
    deployment = json.loads((SECRETS / 'deployment.json').read_text())
    settings = json.loads((SECRETS / 'api-settings.json').read_text())
    keys = az('storage', 'account', 'keys', 'list', '--subscription', deployment['subscription'],
              '--resource-group', deployment['group'], '--account-name', deployment['storage'])
    sas = generate_table_sas(AzureNamedKeyCredential(deployment['storage'], keys[0]['value']),
                            'CurrentState', permission=TableSasPermissions(read=True),
                            start=datetime.now(timezone.utc) - timedelta(minutes=5),
                            expiry=datetime.now(timezone.utc) + timedelta(days=90),
                            start_pk='ohlc|', end_pk='ohlc|~', protocol='https')
    settings['properties']['HISTORY_READ_SAS'] = sas
    # Review/use this file during cutover; existing production settings stay intact.
    target = SECRETS / 'history-api-settings.json'
    target.write_text(json.dumps(settings), encoding='utf-8')
    print('Prepared .secrets/history-api-settings.json; no Azure settings applied.')


if __name__ == '__main__':
    main()
