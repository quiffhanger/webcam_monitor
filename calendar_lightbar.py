"""Renders a 19-segment scrolling timeline of upcoming meetings onto the
keyboard's light bar, driven by the published free/busy ICS feed
(config.CALENDAR_ICS_URL).

Each of the 19 LEDs is a fixed 5-minute bucket anchored on "now": LED index
18 (confirmed physically rightmost) covers [now, now+5m), LED index 0
(confirmed physically leftmost) covers [now+90m, now+95m). As time passes,
a meeting's lit segments drift from the left toward "now" on the right
purely because the bucket boundaries are recomputed relative to the current
clock on every render - no explicit animation is needed.

BUSY/TENTATIVE/OOF handling:
- OOF is always dropped.
- TENTATIVE is dropped if it overlaps any BUSY interval (the busy one wins);
  otherwise it's kept and treated like a normal meeting.
- FREE is always dropped (working-hours placeholder, not a meeting).

Colour assignment is persistent per meeting (keyed by UID + start time) so a
meeting keeps the same colour for as long as it's visible on the bar.
Meetings that mutually overlap in time form a cluster: the first gets a
colour from a varied primary palette, later ones in the same cluster get a
colour from a second, differently-hued palette so they're visually distinguishable
from each other. A segment covered by more than one meeting renders as the
RGB blend of the covering meetings' colours.
"""
import asyncio
import logging
from datetime import datetime, date, timedelta, timezone

import requests
import icalendar
import recurring_ical_events

from . import config

LED_COUNT = 19
BUCKET_MINUTES = 5
LOOKAHEAD_MINUTES = LED_COUNT * BUCKET_MINUTES

# Vivid, well-separated hues across the wheel rather than just R/G/B - more
# variety so consecutive meetings don't feel like they're just alternating
# between the same couple of colours.
PRIMARY_COLOURS = [
    (255, 0, 0),        # Red
    (0, 200, 0),        # Green
    (0, 110, 255),      # Blue
    (255, 140, 0),      # Orange
    (170, 0, 255),      # Purple
    (0, 220, 190),      # Teal
    (255, 90, 170),     # Pink
    (190, 255, 0),      # Chartreuse
]
SECONDARY_COLOURS = [
    (255, 255, 0),      # Yellow
    (255, 0, 220),      # Magenta
    (0, 255, 130),      # Spring green
    (110, 60, 255),     # Indigo
    (255, 60, 0),       # Vermillion
    (0, 180, 255),      # Sky blue
]

FETCH_TIMEOUT = 15
FETCH_RETRY_DELAY = 5


def _normalize(dt):
    """Coerce a date/datetime from icalendar into an aware UTC datetime."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    raise TypeError(f'Unexpected DTSTART/DTEND type: {type(dt)!r}')


def _get_status(event):
    raw = event.get('X-MICROSOFT-CDO-BUSYSTATUS')
    if raw is not None:
        return str(raw).upper()
    summary = str(event.get('SUMMARY', '')).upper()
    if 'TENTATIVE' in summary:
        return 'TENTATIVE'
    if 'BUSY' in summary:
        return 'BUSY'
    if 'AWAY' in summary or 'OOF' in summary:
        return 'OOF'
    return 'FREE'


def _overlaps(a, b):
    return a['start'] < b['end'] and b['start'] < a['end']


def fetch_calendar(url):
    response = requests.get(url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    return icalendar.Calendar.from_ical(response.content)


def expand_events(cal, now, lookahead_minutes=LOOKAHEAD_MINUTES):
    """Expands recurrence and returns raw (key, start, end, status) tuples."""
    window_end = now + timedelta(minutes=lookahead_minutes)
    events = recurring_ical_events.of(cal).between(now, window_end)
    items = []
    for event in events:
        start = _normalize(event['DTSTART'].dt)
        end = _normalize(event['DTEND'].dt)
        status = _get_status(event)
        uid = str(event.get('UID'))
        key = f'{uid}|{start.isoformat()}'
        items.append({'key': key, 'start': start, 'end': end, 'status': status})
    return items


def filter_meetings(items):
    """Drops FREE/OOF, and drops any TENTATIVE that overlaps a BUSY event."""
    busy = [i for i in items if i['status'] == 'BUSY']
    tentative = [i for i in items if i['status'] == 'TENTATIVE']
    kept_tentative = [
        t for t in tentative if not any(_overlaps(t, b) for b in busy)
    ]
    survivors = busy + kept_tentative
    survivors.sort(key=lambda m: m['start'])
    return survivors


def cluster_overlapping(survivors):
    """Groups chronologically-sorted meetings into clusters of mutual overlap."""
    clusters = []
    current = []
    current_end = None
    for meeting in survivors:
        if current and meeting['start'] < current_end:
            current.append(meeting)
            current_end = max(current_end, meeting['end'])
        else:
            if current:
                clusters.append(current)
            current = [meeting]
            current_end = meeting['end']
    if current:
        clusters.append(current)
    return clusters


def _next_unused(palette, avoid):
    for colour in palette:
        if colour not in avoid:
            return colour
    return palette[0]


def assign_colours(clusters, cache):
    """Assigns a persistent colour to every meeting across all clusters.

    Within a cluster, a meeting avoids the colours of every *other* member
    of the cluster it actually overlaps (not just the immediately-preceding
    one in sort order) - a cluster can chain together meetings that aren't
    all pairwise-overlapping, so this checks real overlap each time.

    `cache` maps key -> {'colour': (r,g,b), 'end': datetime} and is mutated
    in place so colours survive across polling cycles.
    """
    last_colour = None
    for cluster in clusters:
        assigned = []
        for index, meeting in enumerate(cluster):
            cached = cache.get(meeting['key'])
            if cached:
                colour = cached['colour']
            else:
                avoid = {c for m, c in assigned if _overlaps(meeting, m)}
                if index == 0:
                    if last_colour is not None:
                        avoid.add(last_colour)
                    palette = PRIMARY_COLOURS
                else:
                    palette = SECONDARY_COLOURS
                colour = _next_unused(palette, avoid)
            cache[meeting['key']] = {'colour': colour, 'end': meeting['end']}
            meeting['colour'] = colour
            assigned.append((meeting, colour))
        last_colour = assigned[-1][1]


def _prune_cache(cache, now):
    expired = [key for key, entry in cache.items() if entry['end'] <= now]
    for key in expired:
        del cache[key]


def build_visible_meetings(url, cache, now=None):
    """Fetch, expand, filter and colour-assign. Returns the visible meeting list."""
    now = now or datetime.now(timezone.utc)
    cal = fetch_calendar(url)
    items = expand_events(cal, now)
    survivors = filter_meetings(items)
    clusters = cluster_overlapping(survivors)
    assign_colours(clusters, cache)
    _prune_cache(cache, now)
    return survivors


def _bucket_window(index, now):
    """Returns (start, end) for LED `index`. LED 18 (rightmost) = [now, now+5m);
    LED 0 (leftmost) = [now+90m, now+95m) - confirmed against the real
    hardware. The window is anchored to `now`, not a fixed wall-clock grid -
    it slides continuously as time passes."""
    offset = BUCKET_MINUTES * (LED_COUNT - 1 - index)
    start = now + timedelta(minutes=offset)
    return start, start + timedelta(minutes=BUCKET_MINUTES)


def compute_leds(meetings, now=None):
    """Maps the current meeting list onto the 19 physical LEDs."""
    now = now or datetime.now(timezone.utc)
    leds = []
    for i in range(LED_COUNT):
        bucket_start, bucket_end = _bucket_window(i, now)
        covering = [
            m for m in meetings if m['start'] < bucket_end and m['end'] > bucket_start
        ]
        if not covering:
            leds.append((0, 0, 0))
        elif len(covering) == 1:
            leds.append(covering[0]['colour'])
        else:
            r = sum(m['colour'][0] for m in covering) // len(covering)
            g = sum(m['colour'][1] for m in covering) // len(covering)
            b = sum(m['colour'][2] for m in covering) // len(covering)
            leds.append((r, g, b))
    return leds


# Fallback wait when there's nothing on the bar to transition (e.g. no
# meetings anywhere in the 95-minute window) - just so the render loop still
# ticks over periodically rather than sleeping indefinitely.
NO_TRANSITION_FALLBACK_SECONDS = 60


def seconds_until_next_transition(meetings, now=None):
    """Seconds until the rendered LED output would next actually change.

    A meeting only enters/leaves LED `i` at two exact instants: when `now`
    crosses `meeting.start - (bucket_end_offset)` (it enters) or
    `meeting.end - (bucket_start_offset)` (it leaves) - the same inequalities
    `compute_leds` checks, solved for `now`. Scheduling the render to wake up
    exactly at the nearest of these gives bang-on-time transitions without
    polling on a fixed interval.
    """
    now = now or datetime.now(timezone.utc)
    candidates = []
    for i in range(LED_COUNT):
        bucket_start, bucket_end = _bucket_window(i, now)
        offset_start = bucket_start - now
        offset_end = bucket_end - now
        for m in meetings:
            enter_at = m['start'] - offset_end
            leave_at = m['end'] - offset_start
            if enter_at > now:
                candidates.append(enter_at)
            if leave_at > now:
                candidates.append(leave_at)
    if not candidates:
        return NO_TRANSITION_FALLBACK_SECONDS
    return max((min(candidates) - now).total_seconds(), 0.5)


def _signature(meetings):
    """Includes start/end, not just identity+colour - a meeting keeps the
    same `key` (uid + original start) and `colour` even when its end time
    is pushed out (running long) or pulled in (ended early/cancelled-short),
    so those fields alone would miss exactly the "meeting changed while
    it's on the bar" case that matters most."""
    return tuple((m['key'], m['start'], m['end'], m['colour']) for m in meetings)


async def watch(poll_interval=300):
    """Yields the current visible-meetings list whenever it changes."""
    cache = {}
    last_signature = None
    while True:
        try:
            meetings = await asyncio.to_thread(
                build_visible_meetings, config.CALENDAR_ICS_URL, cache
            )
            signature = _signature(meetings)
            if signature != last_signature:
                logging.debug(f'Calendar meetings changed: {len(meetings)} visible')
                yield meetings
                last_signature = signature
        except Exception:
            logging.exception('Error fetching/parsing calendar feed, retrying shortly')
            await asyncio.sleep(FETCH_RETRY_DELAY)
            continue
        await asyncio.sleep(poll_interval)
