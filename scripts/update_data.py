#!/usr/bin/env python3
"""Refresh Phantom Peak and London Stadium schedule data.

The site is static. This script is intended to run from GitHub Actions.

London Stadium is scraped from the venue's public events page.
Phantom Peak is read with Playwright from the public ticket page. The Phantom
Peak reader deliberately refuses to delete a known performance merely because
it disappears from sale: a vanished sales slot may simply be sold out. New
slots and changed times can be adopted automatically; missing known slots are
flagged for review after repeated successful full-range scans.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "schedule-data.js"
DIAG_DIR = ROOT / "diagnostics"

PP_URL = "https://www.phantompeak.com/tickets/?flow=lyTxE9UF"
STADIUM_URL = "https://www.london-stadium.com/events/all.html"
WEST_HAM_URL = "https://www.whufc.com/en/matches/mens-team/fixtures"
TARGET_START = date(2026, 12, 4)
TARGET_END = date(2027, 2, 28)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_NAME = {v: k.title() for k, v in MONTHS.items()}
MONTH_RE = "|".join(name.title() for name in MONTHS)
DATE_DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_RE})\s+(20\d{{2}})\b", re.I)
DATE_MDY_RE = re.compile(rf"\b({MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,)?\s+(20\d{{2}})\b", re.I)
ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
TIME_24_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
TIME_12_RE = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?m\.?\b", re.I)
MONTH_YEAR_RE = re.compile(rf"\b({MONTH_RE})\s+(20\d{{2}})\b", re.I)
CANCEL_RE = re.compile(r"\bcancelled\b|\bcanceled\b", re.I)
WEEKDAY_RE = re.compile(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
TIME_TBC_RE = re.compile(r"\b(?:time|start(?:\s+time)?|kick[- ]?off|doors?)?\s*(?:tbc|tba|to be confirmed|to be announced)\b", re.I)
AVAILABILITY_RE = re.compile(r"good availability|selling fast|limited availability|last few|sold out|available", re.I)


def today_label() -> str:
    now = datetime.now(ZoneInfo("Europe/London"))
    return f"{now.day} {now.strftime('%B %Y')}"


def in_target(d: date) -> bool:
    return TARGET_START <= d <= TARGET_END


def load_data() -> dict:
    text = DATA_FILE.read_text(encoding="utf-8")
    prefix = "window.CALENDAR_DATA = "
    if not text.startswith(prefix):
        raise RuntimeError("Unexpected schedule-data.js format")
    return json.loads(text[len(prefix):].strip().removesuffix(";"))


def save_data(data: dict) -> None:
    rendered = "window.CALENDAR_DATA = " + json.dumps(data, indent=2, ensure_ascii=False) + ";\n"
    DATA_FILE.write_text(rendered, encoding="utf-8")


def normalise_time(raw: str) -> str | None:
    raw = raw.strip().lower().replace(".", "")
    m = TIME_12_RE.search(raw)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or "00")
        suffix = m.group(3).lower()
        if suffix == "p" and hour != 12:
            hour += 12
        if suffix == "a" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"
    m = TIME_24_RE.search(raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def parse_date_token(text: str) -> date | None:
    for regex, order in ((DATE_DMY_RE, "dmy"), (DATE_MDY_RE, "mdy")):
        m = regex.search(text)
        if m:
            if order == "dmy":
                day_num, month_name, year_num = m.groups()
            else:
                month_name, day_num, year_num = m.groups()
            try:
                d = date(int(year_num), MONTHS[month_name.lower()], int(day_num))
            except ValueError:
                return None
            return d
    m = ISO_DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def extract_times(text: str) -> set[str]:
    result: set[str] = set()
    for m in TIME_12_RE.finditer(text):
        t = normalise_time(m.group(0))
        if t:
            result.add(t)
    for m in TIME_24_RE.finditer(text):
        t = normalise_time(m.group(0))
        if t:
            result.add(t)
    # PP start times are expected within sensible daytime/evening bounds.
    return {t for t in result if "09:00" <= t <= "23:00"}


def parse_date_time_lines(text: str) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """Extract explicit date/time relationships from plain text.

    Returns (date->times, dates_seen, cancelled_dates).
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    mapping: dict[str, set[str]] = defaultdict(set)
    seen: set[str] = set()
    cancelled: set[str] = set()

    for i, line in enumerate(lines):
        d = parse_date_token(line)
        if not d or not in_target(d):
            continue
        key = d.isoformat()
        seen.add(key)
        chunk = [line]
        for following in lines[i + 1:i + 8]:
            if parse_date_token(following):
                break
            chunk.append(following)
        window = " ".join(chunk)
        mapping[key].update(extract_times(window))
        if CANCEL_RE.search(window):
            cancelled.add(key)
    return mapping, seen, cancelled


def _is_noise_line(line: str) -> bool:
    low = line.casefold()
    if low in {"image", "more info", "all events", "sports", "music", "stadium"}:
        return True
    if low in {m for m in MONTHS}:
        return True
    if re.fullmatch(r"20\d{2}", line):
        return True
    if re.fullmatch(r"monday|tuesday|wednesday|thursday|friday|saturday|sunday", low):
        return True
    if DATE_DMY_RE.search(line) or DATE_MDY_RE.search(line):
        return True
    return False


def parse_stadium_html(html: str) -> list[dict]:
    """Parse every dated London Stadium event in the target range.

    A published time is deliberately optional.  The calendar is useful as a
    crowd warning as soon as the venue announces an event date, so an event
    without a time is retained with ``time: None`` and rendered as "Time TBC".
    A later successful scan replaces the record with the newly published time.
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = [re.sub(r"\s+", " ", s).strip() for s in soup.stripped_strings if s.strip()]
    events: list[dict] = []

    dated_rows: list[tuple[int, date]] = []
    for i, line in enumerate(lines):
        d = parse_date_token(line)
        if d and in_target(d):
            dated_rows.append((i, d))

    for pos, (i, d) in enumerate(dated_rows):
        # Treat the text between this full date and the next full date as one
        # event card.  Cap the window so unrelated footer/navigation text can
        # never be mistaken for the event title on malformed pages.
        next_i = dated_rows[pos + 1][0] if pos + 1 < len(dated_rows) else len(lines)
        block = lines[i:min(next_i, i + 16)]

        time_value = None
        for candidate in block:
            time_value = normalise_time(candidate)
            if time_value:
                break

        event_name = None
        for candidate in block[1:]:
            low = candidate.casefold()
            if _is_noise_line(candidate):
                continue
            if re.fullmatch(r"\d{1,2}\s+[A-Za-z]{3}", candidate):
                continue
            # Calendar metadata such as "Saturday – 15:00 PM" or
            # "Saturday – Time TBC" is not an event title.
            if WEEKDAY_RE.search(candidate):
                continue
            if TIME_TBC_RE.search(candidate):
                continue
            if normalise_time(candidate):
                continue
            if low.startswith(("more info", "buy tickets", "tickets", "hospitality")):
                continue
            event_name = candidate
            break

        if event_name:
            block_text = " ".join(block)
            events.append({
                "date": d.isoformat(),
                "time": time_value,
                "name": event_name,
                "status": "cancelled" if CANCEL_RE.search(block_text) else "scheduled",
            })

    unique = []
    seen = set()
    for event in events:
        key = (event["date"], event.get("time"), event["name"])
        if key not in seen:
            unique.append(event)
            seen.add(key)
    return unique


def scrape_stadium() -> list[dict]:
    for attempt in range(3):
        try:
            response = requests.get(
                STADIUM_URL,
                timeout=(10, 35),
                headers={"User-Agent": "PPEC-Stratford-Crowd-Checker/2.0 (+GitHub Pages)"},
            )
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise
            delay = 2 ** (attempt + 1)
            print(f"London Stadium: request failed; retrying in {delay}s ({attempt + 2}/3)")
            time.sleep(delay)
    unique = parse_stadium_html(response.text)
    if not unique:
        raise RuntimeError("No London Stadium events found in target range; refusing to overwrite existing data")
    return unique


def parse_west_ham_html(html: str) -> list[dict]:
    """Decode the fixture page's JSON transport without executing JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    chunks = []
    for script in soup.find_all("script"):
        match = re.fullmatch(r"self\.__next_f\.push\((.*)\);?", script.get_text().strip(), re.S)
        if match:
            item = json.loads(match.group(1))
            if isinstance(item, list) and len(item) == 2 and item[0] == 1 and isinstance(item[1], str):
                chunks.append(item[1])

    def fixtures(value):
        if isinstance(value, dict):
            if "fixtureProps" in value:
                yield value
            for child in value.values():
                yield from fixtures(child)
        elif isinstance(value, list):
            for child in value:
                yield from fixtures(child)

    events = {}
    # React transport records start with a hexadecimal ID. Decode only JSON
    # records; module references and text records are not fixture objects.
    for record in re.finditer(r"(?:^|\n)[0-9a-f]+:([\[{].*)", "".join(chunks)):
        try:
            value, _ = json.JSONDecoder().raw_decode(record.group(1))
        except ValueError:
            continue
        for row in fixtures(value):
            fixture = row["fixtureProps"]
            if fixture.get("homeOrAway") != "Home" or fixture.get("matchLocation") != "London Stadium":
                continue
            if fixture.get("homeTeam", {}).get("clubName") != "West Ham United":
                raise ValueError("Unexpected home team in West Ham fixture")
            start = datetime.fromisoformat(row["kickOffUtc"].replace("Z", "+00:00"))
            if start.tzinfo is None:
                raise ValueError("Fixture kickoff has no timezone")
            local = start.astimezone(ZoneInfo("Europe/London"))
            if not in_target(local.date()):
                continue
            status = fixture.get("matchStatus")
            if status not in {"PreMatch", "Postponed", "Cancelled", "Canceled"}:
                raise ValueError(f"Unsupported fixture status: {status}")
            opponent = fixture["awayTeam"]["clubName"]
            identity = fixture["id"]
            if not opponent or not identity:
                raise ValueError("Fixture is missing its identity or opponent")
            tbc = row.get("kickOffTbc")
            if tbc not in (None, False, True, "$undefined"):
                raise ValueError("Unknown kickoff confirmation flag")
            event = {
                "date": local.date().isoformat(),
                "time": None if tbc is True or status == "Postponed" else local.strftime("%H:%M"),
                "name": f"West Ham United v {opponent}",
                "status": "cancelled" if status in {"Cancelled", "Canceled"} else "scheduled",
                "sourceUrl": WEST_HAM_URL, "fixtureId": identity,
            }
            if identity in events and events[identity] != event:
                raise ValueError("Conflicting fixture records")
            events[identity] = event
    result = sorted(events.values(), key=lambda event: (event["date"], event["name"]))
    if not result or {event["date"][:7] for event in result} != {"2026-12", "2027-01", "2027-02"}:
        raise ValueError("West Ham fixture response does not cover the target months")
    return result


def scrape_west_ham() -> list[dict]:
    response = requests.get(WEST_HAM_URL, timeout=(10, 35))
    response.raise_for_status()
    return parse_west_ham_html(response.text)


def merge_west_ham(source: dict, events: list[dict]) -> None:
    """Update positively identified fixtures; never remove absent events."""
    existing = source["events"]
    for event in events:
        matches = [old for old in existing if old.get("fixtureId") == event["fixtureId"]]
        if not matches:
            matches = [old for old in existing if old["name"].casefold() == event["name"].casefold()]
            # Names can recur in cup and league games. Only migrate a legacy
            # record when the match is unambiguous on both sides.
            if sum(new["name"] == event["name"] for new in events) != 1:
                raise ValueError("Ambiguous fixture names; retaining existing events")
        if len(matches) > 1:
            raise ValueError("Ambiguous existing fixture; retaining existing events")
        if matches:
            matches[0].update(event)
        else:
            existing.append(copy.deepcopy(event))
    existing.sort(key=lambda event: (event["date"], event["name"]))


def refresh_stadium_backup(source: dict) -> None:
    backup = source.setdefault("footballRefresh", {"lastSuccessfulRefreshAt": None})
    backup["lastAttemptAt"] = datetime.now(timezone.utc).isoformat()
    backup["sourceUrl"] = WEST_HAM_URL
    try:
        events = scrape_west_ham()
        candidate = copy.deepcopy(source)
        merge_west_ham(candidate, events)
        source["events"] = candidate["events"]
        backup.update(status="success", error=None,
                      lastSuccessfulRefreshAt=datetime.now(timezone.utc).isoformat())
        source["refresh"]["status"] = "partial-football-refresh"
        print(f"London Stadium: official West Ham backup refreshed {len(events)} home fixtures; full venue coverage unavailable")
    except Exception as exc:
        backup.update(status="error-fallback-retained", error=str(exc))
        print(f"West Ham backup unavailable; known events retained: {exc}")


PP_ORGANIZATION_ID = "698c9b48693e081bc997a661"


def is_pp_widget_response(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.onthestage.tickets"
        and parsed.path == f"/api/widget/{PP_ORGANIZATION_ID}/all"
    )


def parse_phantom_peak_widget(payload: dict) -> dict:
    """Read explicit event starts from the booking widget's complete response.

    Refuse partial/changed schemas. Each production's advertised date inventory
    must match its event records before any changes or missing-date counts can
    be published. Sold-out/off-sale records still represent performances.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("productions"), list):
        raise ValueError("Missing widget productions list")
    if not payload["productions"]:
        raise ValueError("Empty widget productions list; retaining known data")
    mapping: dict[str, set[str]] = defaultdict(set)
    active_dates: set[str] = set()
    cancelled_dates: set[str] = set()
    identities: dict[str, tuple] = {}
    event_count = 0

    def timestamp(raw):
        if not isinstance(raw, str):
            raise ValueError("Missing explicit event start timestamp")
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Event start timestamp has no timezone")
        return dt

    for production in payload["productions"]:
        if not isinstance(production, dict):
            raise ValueError("Invalid production record")
        if production.get("organization", {}).get("_id") != PP_ORGANIZATION_ID:
            raise ValueError("Unexpected production organization")
        if production.get("timezone") != "Europe/London":
            raise ValueError("Unexpected production timezone")
        dates, events = production.get("dates"), production.get("events")
        if not isinstance(dates, list) or not isinstance(events, list) or not dates or not events:
            raise ValueError("Missing production date/event inventory")
        expected = {timestamp(raw) for raw in dates}
        actual = set()
        for event in events:
            if not isinstance(event, dict) or event.get("id") is None:
                raise ValueError("Missing event identity")
            if event.get("timezone") != "Europe/London":
                raise ValueError("Unexpected event timezone")
            start = timestamp(event.get("start_date"))
            actual.add(start)
            status = event.get("event_status")
            if not isinstance(status, str) or not status:
                raise ValueError("Missing event status")
            identity = str(event["id"])
            signature = (start, status)
            if identity in identities and identities[identity] != signature:
                raise ValueError("Conflicting event records")
            identities[identity] = signature
            local = start.astimezone(ZoneInfo("Europe/London"))
            if not in_target(local.date()):
                continue
            key = local.date().isoformat()
            # Do not infer cancellation from availability or sales status.
            if status.casefold() in {"cancelled", "canceled"}:
                cancelled_dates.add(key)
            else:
                active_dates.add(key)
                mapping[key].add(local.strftime("%H:%M"))
            event_count += 1
        if actual != expected:
            raise ValueError("Event records do not match the production date inventory")

    seen = sorted(active_dates | cancelled_dates)
    months = sorted({d[:7] for d in seen})
    # Preserve the existing conservative range gate, now backed by explicit
    # structured event records rather than a heading or arbitrary JSON text.
    successful = bool(seen) and (
        set(months).issuperset({"2026-12", "2027-01", "2027-02"})
        and len(seen) >= 20
        and seen[0] <= "2026-12-20"
        and seen[-1] >= "2027-02-15"
    )
    return {
        "successful": successful,
        "times": {key: sorted(values) for key, values in sorted(mapping.items())},
        "seenDates": seen,
        "availableDates": sorted(active_dates),
        # The existing data model cancels whole dates. Never cancel a date
        # that still has another active performance.
        "cancelledDates": sorted(cancelled_dates - active_dates),
        "scannedMonths": months if successful else [],
        "method": "structured-widget-response",
        "eventCount": event_count,
    }


async def scrape_phantom_peak() -> dict:
    from playwright.async_api import async_playwright

    DIAG_DIR.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="en-GB", timezone_id="Europe/London")
        try:
            # Discover the response through the official ticket page so its
            # campaign and widget configuration stay under the venue's control.
            # Waiting for this response also handles late shadow-DOM rendering.
            async with page.expect_response(
                lambda response: is_pp_widget_response(response.url), timeout=90000
            ) as response_info:
                await page.goto(PP_URL, wait_until="domcontentloaded", timeout=90000)
            response = await response_info.value
            if response.status != 200:
                raise RuntimeError(f"Phantom Peak widget returned HTTP {response.status}")
            payload = await response.json()
            scan = parse_phantom_peak_widget(payload)
            (DIAG_DIR / "phantom-peak-scan.txt").write_text(
                "PPEC Phantom Peak scan diagnostics\n"
                f"Method: {scan['method']}\n"
                f"Successful full-range calendar scan: {scan['successful']}\n"
                f"Months covered: {', '.join(scan['scannedMonths']) or '-'}\n"
                f"Event records: {scan['eventCount']}\n"
                f"Live performance dates: {len(scan['availableDates'])}\n"
                + "\nEXTRACTED TIMES\n"
                + "\n".join(f"{d}: {', '.join(times)}" for d, times in scan["times"].items()),
                encoding="utf-8",
            )
            return scan
        finally:
            try:
                await page.screenshot(path=str(DIAG_DIR / "phantom-peak-page.png"), full_page=True)
            except Exception:
                pass
            await browser.close()


def merge_phantom_peak(data: dict, scan: dict) -> bool:
    pp = data["phantomPeak"]
    pp["lastChecked"] = today_label()
    if not scan["successful"]:
        pp["liveSyncStatus"] = "scan-failed-fallback-retained"
        return False

    pp["liveSyncStatus"] = "live-calendar"
    pp["lastSuccessfulLiveSync"] = today_label()
    discovered = scan["times"]
    available = set(scan.get("availableDates", []))
    seen_dates = set(scan.get("seenDates", []))
    cancelled = set(scan["cancelledDates"])
    scanned_months = set(scan.get("scannedMonths", []))
    existing = {p["date"]: p for p in pp["performances"]}

    # A live availability marker is strong evidence that a performance exists,
    # even if the widget does not expose its start time in a machine-readable
    # form. New dates therefore appear immediately as Time TBC rather than being
    # silently missed.
    for key in sorted(available | set(discovered)):
        times = discovered.get(key, [])
        if key in existing:
            perf = existing[key]
            if times:
                perf["times"] = times
                perf["basis"] = "live-ticketing"
            elif perf.get("basis") not in {"official-opening-schedule", "live-ticketing"}:
                perf["basis"] = "live-calendar-confirmed"
            perf["listingStatus"] = "normal"
            perf["missingLiveRuns"] = 0
            if key not in cancelled:
                perf["status"] = "scheduled"
        else:
            perf = {
                "date": key,
                "times": times,
                "note": None,
                "basis": "live-ticketing" if times else "live-calendar-confirmed",
                "status": "scheduled",
                "listingStatus": "normal",
                "missingLiveRuns": 0,
            }
            pp["performances"].append(perf)
            existing[key] = perf

    for key, perf in existing.items():
        if key in cancelled:
            perf["status"] = "cancelled"
            perf["listingStatus"] = "normal"
            perf["missingLiveRuns"] = 0
            continue
        month_key = key[:7]
        if key in available or key in discovered:
            perf["missingLiveRuns"] = 0
            if perf.get("status") != "cancelled":
                perf["listingStatus"] = "normal"
            continue
        # Only assess disappearance when that month was definitely scanned.
        # Never auto-delete: sold-out slots can disappear from availability.
        if month_key in scanned_months:
            perf["missingLiveRuns"] = int(perf.get("missingLiveRuns", 0)) + 1
            if perf["missingLiveRuns"] >= 2:
                perf["listingStatus"] = "review"
        elif key in seen_dates:
            perf["missingLiveRuns"] = 0

    pp["performances"].sort(key=lambda p: p["date"])
    return True

SOURCE_NAMES = {"londonStadium": "London Stadium", "phantomPeak": "Phantom Peak"}
STALE_AFTER = timedelta(hours=48)


def initialise_refresh_metadata(data: dict, now: datetime) -> None:
    for key in SOURCE_NAMES:
        source = data[key]
        if "refresh" in source:
            continue
        # Legacy Stadium lastChecked only advanced on success. PP lastChecked
        # includes failed checks, so only its successful-sync label is evidence.
        legacy = source.get("lastChecked" if key == "londonStadium" else "lastSuccessfulLiveSync")
        last_success = None
        if legacy:
            try:
                last_success = datetime.strptime(legacy, "%d %B %Y").replace(
                    tzinfo=ZoneInfo("Europe/London")
                ).astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
        source["refresh"] = {
            "status": "unknown", "lastAttemptAt": None,
            "lastSuccessfulRefreshAt": last_success,
            "monitoringStartedAt": now.isoformat(), "error": None,
        }


def stale_sources(data: dict, now: datetime) -> list[str]:
    stale = []
    for key, name in SOURCE_NAMES.items():
        meta = data[key].get("refresh", {})
        timestamp = meta.get("lastSuccessfulRefreshAt") or meta.get("monitoringStartedAt")
        try:
            baseline = datetime.fromisoformat(timestamp)
            if baseline.tzinfo is None:
                raise ValueError("Freshness timestamps must include a timezone")
            expired = now - baseline >= STALE_AFTER
        except (TypeError, ValueError):
            expired = True
        if expired:
            stale.append(name)
    return stale


def refresh_sources(data: dict, *, stadium_only=False, phantom_only=False) -> None:
    initialise_refresh_metadata(data, datetime.now(timezone.utc))
    for key, name in SOURCE_NAMES.items():
        if (key == "londonStadium" and phantom_only) or (key == "phantomPeak" and stadium_only):
            continue
        source = data[key]
        meta = source["refresh"]
        meta["lastAttemptAt"] = datetime.now(timezone.utc).isoformat()
        try:
            # Work on a copy: even an exception partway through a merge must
            # never publish partially changed performance records.
            candidate = copy.deepcopy(data)
            if key == "londonStadium":
                events = scrape_stadium()
                candidate[key]["events"] = events
                candidate[key]["lastChecked"] = today_label()
                live = True
            else:
                scan = asyncio.run(scrape_phantom_peak())
                live = merge_phantom_peak(candidate, scan)
            data[key] = candidate[key]
            meta = data[key]["refresh"]
            meta["status"] = "success" if live else "fallback-retained"
            meta["error"] = None if live else "Full-range live scan could not be verified"
            if live:
                meta["lastSuccessfulRefreshAt"] = datetime.now(timezone.utc).isoformat()
            print(f"{name}: {meta['status']}")
        except Exception as exc:
            meta["status"] = "error-fallback-retained"
            meta["error"] = str(exc)
            if key == "londonStadium":
                refresh_stadium_backup(source)
            if key == "phantomPeak":
                source["lastChecked"] = today_label()
                source["liveSyncStatus"] = "scan-error-fallback-retained"
                try:
                    DIAG_DIR.mkdir(exist_ok=True)
                    (DIAG_DIR / "phantom-peak-error.txt").write_text(str(exc) + "\n", encoding="utf-8")
                except OSError as diagnostic_error:
                    print(f"Could not write diagnostics: {diagnostic_error}")
            print(f"{name}: refresh unavailable; known data retained: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stadium-only", action="store_true")
    parser.add_argument("--phantom-only", action="store_true")
    parser.add_argument("--check-freshness", action="store_true", help="Check saved metadata without scraping or writing")
    parser.add_argument("--defer-freshness-check", action="store_true", help="Save updates now; workflow checks freshness after committing")
    args = parser.parse_args()
    if args.stadium_only and args.phantom_only:
        raise SystemExit("Choose only one of --stadium-only or --phantom-only")

    data = load_data()
    if not args.check_freshness:
        refresh_sources(data, stadium_only=args.stadium_only, phantom_only=args.phantom_only)
        save_data(data)
    if not args.defer_freshness_check:
        stale = stale_sources(data, datetime.now(timezone.utc))
        if stale:
            raise SystemExit("Published data is stale (48 hours without a successful refresh): " + ", ".join(stale))


if __name__ == "__main__":
    main()
