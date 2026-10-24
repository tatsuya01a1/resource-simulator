"""Shared UTC keys and strict, public OHLC contract (no Worker dependencies)."""
import calendar
from datetime import datetime, timedelta, timezone
import math
from uuid import UUID

JST = timezone(timedelta(hours=9))


def instant(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('timezone required')
    return at.astimezone(timezone.utc)


def stamp(at):
    return at.astimezone(timezone.utc).isoformat(timespec='microseconds')


def months_before(at, count):
    local = at.astimezone(JST)
    index = local.year * 12 + local.month - 1 - count
    year, month = divmod(index, 12)
    return local.replace(year=year, month=month + 1,
                         day=min(local.day, calendar.monthrange(year, month + 1)[1]))


def partition(run_id, resource_id):
    if str(UUID(run_id)) != run_id or resource_id not in ('resource', 'beta'):
        raise ValueError('invalid history identity')
    return f'ohlc|{run_id}|{resource_id}'


def candle(value):
    fields = ('minute_start', 'open', 'high', 'low', 'close', 'sample_count',
              'first_observed_at', 'last_observed_at')
    result = {name: value[name] for name in fields}
    for name in ('open', 'high', 'low', 'close'):
        if type(result[name]) not in (int, float) or not math.isfinite(result[name]):
            raise ValueError('invalid OHLC number')
    if (type(result['sample_count']) is not int or result['sample_count'] < 1
            or not 0 <= result['low'] <= min(result['open'], result['close'])
            or result['high'] < max(result['open'], result['close'], result['low'])):
        raise ValueError('invalid OHLC range')
    start, first, last = [instant(result[k]) for k in
                          ('minute_start', 'first_observed_at', 'last_observed_at')]
    if start.second or start.microsecond or not start <= first <= last < start + timedelta(minutes=1):
        raise ValueError('invalid OHLC time')
    for key, at in zip(('minute_start', 'first_observed_at', 'last_observed_at'), (start, first, last)):
        result[key] = stamp(at)
    return result
