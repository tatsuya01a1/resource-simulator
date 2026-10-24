import json
import os

import azure.functions as func
from azure.core.credentials import AzureSasCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient

from current_service import read_current
from history_service import read_history, OutsideHistory

def current(req: func.HttpRequest) -> func.HttpResponse:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    try:
        with TableClient(os.environ["TABLE_ENDPOINT"], os.environ.get("TABLE_NAME", "CurrentState"),
                         credential=AzureSasCredential(os.environ["TABLE_READ_SAS"]),
                         connection_timeout=2, read_timeout=3, retry_total=0) as client:
            value = read_current(client)
        value["build_commit"] = os.environ.get("BUILD_COMMIT", "unknown")
        value['history_available'] = bool(os.environ.get('HISTORY_READ_SAS'))
        return func.HttpResponse(json.dumps(value, allow_nan=False), headers=headers, mimetype="application/json")
    except ResourceNotFoundError:
        return func.HttpResponse('{"error":"not_initialized"}', status_code=404,
                                 headers=headers, mimetype="application/json")
    except Exception:
        # Never return SDK exceptions, storage URLs with SAS, or configuration.
        return func.HttpResponse('{"error":"temporarily_unavailable"}', status_code=503,
                                 headers=headers, mimetype="application/json")


def ohlc(req: func.HttpRequest) -> func.HttpResponse:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    try:
        if not all(req.params.get(key) for key in ('run_id', 'resource_id', 'from', 'to')):
            raise ValueError('missing history parameters')
        if not os.environ.get('TABLE_ENDPOINT') or not os.environ.get('HISTORY_READ_SAS'):
            raise RuntimeError('history not configured')
        with TableClient(os.environ['TABLE_ENDPOINT'], os.environ.get('TABLE_NAME', 'CurrentState'),
                         credential=AzureSasCredential(os.environ['HISTORY_READ_SAS']),
                         connection_timeout=2, read_timeout=3, retry_total=0) as client:
            value = read_history(client, req.params)
        return func.HttpResponse(json.dumps(value, allow_nan=False), headers=headers, mimetype='application/json')
    except OutsideHistory:
        status, error = 416, 'outside_history_window'
    except (ValueError, KeyError, TypeError):
        status, error = 400, 'invalid_history_request'
    except Exception:
        status, error = 503, 'history_unavailable'
    return func.HttpResponse(json.dumps(dict(error=error)), status_code=status,
                             headers=headers, mimetype='application/json')
