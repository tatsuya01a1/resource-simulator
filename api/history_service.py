"""Bounded key-range queries. Cursor is a scoped last-key, never an SDK token."""
import base64
from datetime import datetime, timedelta, timezone
from itertools import islice
import json

try:
    from .history_contract import candle, instant, months_before, partition, stamp
except ImportError:
    from history_contract import candle, instant, months_before, partition, stamp


class OutsideHistory(ValueError):
    pass


def read_history(client, params, now=None):
    now = now or datetime.now(timezone.utc)
    anchor = instant(params['as_of']) if params.get('as_of') else now
    if anchor > now + timedelta(seconds=2) or now - anchor > timedelta(minutes=5):
        raise ValueError('expired history anchor')
    run, resource = params['run_id'], params['resource_id']
    pk = partition(run, resource)
    start, end = instant(params['from']), instant(params['to'])
    manifest = params.get('mode') == 'manifest'
    if not start < end or (not manifest and end - start > timedelta(days=7)):
        raise ValueError('expected half-open interval of at most seven days')
    # Round down so the boundary minute remains viewable without inventing samples.
    earliest = months_before(anchor, 3).replace(second=0, microsecond=0)
    if start < earliest or end > anchor:
        raise OutsideHistory('outside calendar history window')
    if manifest:
        query = 'PartitionKey eq @pk and RowKey ge @start and RowKey lt @end'
        arguments = dict(pk=pk, start='day|' + start.date().isoformat(),
                         end='day|' + (end.date() + timedelta(days=1)).isoformat())
        entities = client.query_entities(query, parameters=arguments, results_per_page=1000,
                                         select=['RowKey', 'revision'])
        revisions = {e['RowKey'][4:]: e['revision'] for e in islice(entities, 95)}
        return dict(run_id=run, resource_id=resource, revisions=revisions, api_time=stamp(now))
    limit = int(params.get('limit', '10080'))
    resolution = int(params.get('resolution_minutes', '1'))
    if not 1 <= limit <= 10080 or not 1 <= resolution <= 1440:
        raise ValueError('invalid limit')
    scope = [run, resource, stamp(start), stamp(end), resolution]
    after = None
    if params.get('cursor'):
        if len(params['cursor']) > 1024:
            raise ValueError('invalid cursor')
        try:
            decoded = json.loads(base64.urlsafe_b64decode(params['cursor']).decode())
            after = decoded[5]
            if decoded[:5] != scope or len(decoded) != 6 or not start <= instant(after) < end:
                raise ValueError('cursor scope mismatch')
        except Exception as exc:
            raise ValueError('invalid cursor') from exc
    query = 'PartitionKey eq @pk and RowKey ge @start and RowKey lt @end'
    arguments = dict(pk=pk, start=stamp(start), end=stamp(end))
    if after:
        query += ' and RowKey gt @after'
        arguments['after'] = stamp(instant(after))
    entities = list(islice(client.query_entities(query, parameters=arguments,
                                                results_per_page=min(1000, limit + 1)), limit + 1))
    rows = []
    for entity in entities[:limit]:
        try:
            item = candle(json.loads(entity['payload']))
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('invalid cloud history') from exc
        if entity['PartitionKey'] != pk or entity['RowKey'] != item['minute_start']:
            raise RuntimeError('cloud history identity mismatch')
        rows.append(item)
    cursor = None
    if len(entities) > limit:
        cursor = base64.urlsafe_b64encode(json.dumps(scope + [rows[-1]['minute_start']]).encode()).decode()
    return dict(run_id=run, resource_id=resource, candles=aggregate(rows, resolution) if resolution > 1 else rows, cursor=cursor,
                earliest=stamp(earliest), api_time=stamp(now), finalized_only=True)


def aggregate(rows, resolution):
    """Render projection only. Never aggregate across a missing minute."""
    result = []
    previous = None
    for row in rows:
        at = instant(row['minute_start'])
        bucket = int(at.timestamp()) // (resolution * 60)
        if (not result or previous != at - timedelta(minutes=1)
                or int(instant(result[-1]['minute_start']).timestamp()) // (resolution * 60) != bucket):
            result.append(dict(row, minutes=1, end=stamp(at + timedelta(minutes=1)),
                               partial=row['sample_count'] < 60))
        else:
            last = result[-1]
            last.update(high=max(last['high'], row['high']), low=min(last['low'], row['low']),
                        close=row['close'], last_observed_at=row['last_observed_at'],
                        sample_count=last['sample_count'] + row['sample_count'], minutes=last['minutes'] + 1,
                        end=stamp(at + timedelta(minutes=1)), partial=last['partial'] or row['sample_count'] < 60)
        previous = at
    return result
