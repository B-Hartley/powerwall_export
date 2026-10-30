"""Conservative one-day future tariff overlay for weekday/weekend Tesla TOU profiles.

Refuse unfamiliar structures rather than risk altering a user's normal rate plan.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .tariff import ExportError, normalize_tariff, tariff_matches


def _md(d):
    return (d.month, d.day)


def _date(md):
    return date(2000, md[0], md[1])


def _spans(p):
    """Expand Tesla day-of-week rules into non-wrapping (dow,start,end) spans.

    Tesla permits overnight periods (e.g. 23:30 -> 05:30) and a
    whole-day 00:00 -> 00:00 period. After midnight belongs to the
    following weekday, including Saturday -> Sunday.
    """
    d0,d1=p.get('fromDayOfWeek'),p.get('toDayOfWeek')
    if not all(isinstance(v,int) and not isinstance(v,bool) and 0<=v<=6 for v in (d0,d1)) or d0>d1:
        raise ExportError('Unsupported weekday range in tariff period. No write.')
    values=(p.get('fromHour'),p.get('fromMinute'),p.get('toHour'),p.get('toMinute'))
    if not all(isinstance(v,int) and not isinstance(v,bool) for v in values):
        raise ExportError('Unsupported time of day in tariff period. No write.')
    ah,am,bh,bm=values
    if not(0<=ah<=23 and 0<=bh<=23 and 0<=am<=59 and 0<=bm<=59):
        raise ExportError('Invalid hour or minute in tariff period. No write.')
    a,b=ah*60+am,bh*60+bm
    spans=[]
    for day in range(d0,d1+1):
        if a == b:
            if a != 0:
                raise ExportError('Ambiguous equal-start/end tariff period. No write.')
            spans.append((day,0,1440))
        elif b > a:
            spans.append((day,a,b))
        else:
            spans.append((day,a,1440))
            spans.append(((day+1)%7,0,b))
    return spans

def _p(a,b,d0=0,d1=6):
    return {'fromDayOfWeek':d0,'toDayOfWeek':d1,'fromHour':a//60,'fromMinute':a%60,
            'toHour':(b//60)%24,'toMinute':b%60}


def _validate_day(intervals):
    intervals.sort()
    cursor=0
    for a,b,_ in intervals:
        if a!=cursor:
            raise ExportError('Normal weekday/weekend tariff has gaps or overlapping periods. No write.')
        cursor=b
    if cursor!=1440:
        raise ExportError('Normal weekday/weekend tariff does not cover 24 hours. No write.')


def _overlay_side(side, day, start_minute, end_minute, event_price):
    seasons = side['seasons']
    def covers(s):
        lo=(s['fromMonth'],s['fromDay'])
        hi=(s['toMonth'],s['toDay'])
        md=_md(day)
        return lo<=md<=hi if lo<=hi else (md>=lo or md<=hi)
    matching = [key for key,s in seasons.items() if covers(s)]
    if len(matching) != 1:
        raise ExportError('Cannot identify one unambiguous original tariff season. No write.')
    key = matching[0]
    original = seasons[key]
    # Expand all weekday ranges to independent days before modifying only the
    # event weekday. Tesla uses Monday=0 ... Sunday=6, matching Python weekday().
    event_dow=day.weekday()
    by_day={dow:[] for dow in range(7)}
    for label,block in original['tou_periods'].items():
        if label not in side['energy_charges'][key]['rates']:
            raise ExportError('Tariff period missing corresponding energy price. No write.')
        for period in block['periods']:
            for dow,a,b in _spans(period):
                by_day[dow].append((a,b,label))
    for intervals in by_day.values():
        _validate_day(intervals)
    pieces={}
    for dow,intervals in by_day.items():
        for a,b,label in intervals:
            # Retain the complete normal profile on every other weekday.
            if dow!=event_dow:
                pieces.setdefault(label,[]).append(_p(a,b,dow,dow))
                continue
            if a<start_minute:
                x,y=a,min(b,start_minute)
                if x<y: pieces.setdefault(label,[]).append(_p(x,y,dow,dow))
            if b>end_minute:
                x,y=max(a,end_minute),b
                if x<y: pieces.setdefault(label,[]).append(_p(x,y,dow,dow))
    special='HA_SESSION'
    if special in original['tou_periods'] or special in side['energy_charges'][key]['rates']:
        raise ExportError('Tariff already has a HA_SESSION period. No write.')
    pieces[special]=[_p(start_minute,end_minute,event_dow,event_dow)]
    old_rates=side['energy_charges'][key]['rates']
    def assign(label,first,last,periods,extra_rate=None):
        if label in seasons:
            raise ExportError('Conflicting season label. No write.')
        new=deepcopy(original)
        new.update({'fromMonth':first.month,'fromDay':first.day,'toMonth':last.month,'toDay':last.day})
        new['tou_periods']={k:deepcopy(v) if isinstance(v,dict) else {'periods':v} for k,v in periods.items()}
        seasons[label]=new
        for charges_key in ('energy_charges','demand_charges'):
            charges=side[charges_key]
            if key in charges:
                charges[label]=deepcopy(charges[key])
        if extra_rate is not None:
            side['energy_charges'][label]['rates'][special]=extra_rate
    first=_date((original['fromMonth'],original['fromDay']))
    last=_date((original['toMonth'],original['toDay']))
    event=_date(_md(day))
    other_range=None
    if first>last:
        # Cyclic Tesla season, e.g. Oct 28 -> Oct 23. Split into its
        # Jan/Dec portions before isolating the event day.
        if event>=first:
            other_range=(date(2000,1,1),last)
            last=date(2000,12,31)
        elif event<=last:
            other_range=(first,date(2000,12,31))
            first=date(2000,1,1)
        else:
            raise ExportError('Session date is outside the tariff season. No write.')
    if not first<=event<=last:
        raise ExportError('Session date is outside the tariff season. No write.')
    del seasons[key]
    if other_range is not None:
        assign(f'{key}_HA_OTHER',other_range[0],other_range[1],deepcopy(original['tou_periods']))
    if event>first:
        before=event-timedelta(days=1)
        assign(f'{key}_HA_BEFORE',first,before,deepcopy(original['tou_periods']))
    assign(f'{key}_HA_EVENT',event,event,pieces,event_price)
    if event<last:
        after=event+timedelta(days=1)
        assign(f'{key}_HA_AFTER',after,last,deepcopy(original['tou_periods']))
    for charges_key in ('energy_charges','demand_charges'):
        side[charges_key].pop(key,None)


def build_advance_tariff(original,start,end,time_zone,buy_price=2.10,sell_price=2.00):
    original=normalize_tariff(original)
    if start.tzinfo is None or end.tzinfo is None:
        raise ExportError('Calendar event timestamps need time zones. No write.')
    zone=ZoneInfo(time_zone)
    a,b=start.astimezone(zone),end.astimezone(zone)
    if a.date()!=b.date() or a.utcoffset()!=b.utcoffset():
        raise ExportError('Advance test supports a single local calendar day, without clock changes. No write.')
    if a.second or b.second or a.microsecond or b.microsecond:
        raise ExportError('Session times must be exact minutes. No write.')
    x=a.hour*60+a.minute
    y=b.hour*60+b.minute
    if not 0 < y-x <= 360:
        raise ExportError('Session must be between 1 and 360 minutes. No write.')
    if not (0<=sell_price<=buy_price<=10):
        raise ExportError('Invalid temporary tariff prices. No write.')
    result=deepcopy(original)
    for side,price in ((result,buy_price),(result['sell_tariff'],sell_price)):
        _overlay_side(side,a.date(),x,y,price)
        side['name']='HA prepared Octopus Saving Session (temporary)'
        side['code']='HA-PWE-PREPARED'
    result['name']='HA prepared Octopus Saving Session (temporary)'
    result['code']='HA-PWE-PREPARED'
    result=normalize_tariff(result)
    if tariff_matches(result,original):
        raise ExportError('No effective tariff change detected. No write.')
    return result
