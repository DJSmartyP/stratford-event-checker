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
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "schedule-data.js"
DIAG_DIR = ROOT / "diagnostics"

PP_URL = "https://www.phantompeak.com/tickets/?flow=lyTxE9UF"
STADIUM_URL = "https://www.london-stadium.com/events/all.html"
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
    response = requests.get(
        STADIUM_URL,
        timeout=35,
        headers={"User-Agent": "PPEC-Stratford-Crowd-Checker/2.0 (+GitHub Pages)"},
    )
    response.raise_for_status()
    unique = parse_stadium_html(response.text)
    if not unique:
        raise RuntimeError("No London Stadium events found in target range; refusing to overwrite existing data")
    return unique


async def _frame_snapshot(frame) -> dict:
    try:
        body = await frame.locator("body").inner_text(timeout=3000)
    except Exception:
        body = ""
    try:
        items = await frame.locator("button, a, [role='button'], [aria-label], [data-date], [data-time], time").evaluate_all(
            """els => els.slice(0, 1200).map(el => ({
                text: (el.innerText || el.textContent || '').trim(),
                aria: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                dataDate: el.getAttribute('data-date') || '',
                dataTime: el.getAttribute('data-time') || '',
                datetime: el.getAttribute('datetime') || ''
            }))"""
        )
    except Exception:
        items = []
    return {"url": frame.url, "body": body, "items": items}


async def _interactive_times(frame) -> set[str]:
    try:
        texts = await frame.locator("button, a, [role='button'], option, [data-time], time").evaluate_all(
            """els => els.filter(el => {
                const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && r.width > 0 && r.height > 0;
            }).slice(0, 800).map(el => [el.innerText || el.textContent || '', el.getAttribute('aria-label') || '', el.getAttribute('data-time') || '', el.getAttribute('datetime') || ''].join(' '))"""
        )
    except Exception:
        return set()
    times = set()
    for text in texts:
        stripped = re.sub(r"\s+", " ", text).strip()
        # Stronger filter than body text: the control itself should be predominantly a time/status.
        if len(stripped) > 45:
            continue
        times.update(extract_times(stripped))
    return times


async def _month_year(frame) -> tuple[int, int] | None:
    try:
        text = await frame.locator("body").inner_text(timeout=2000)
    except Exception:
        return None
    matches = MONTH_YEAR_RE.findall(text)
    candidates = []
    for month_name, year_num in matches:
        d = date(int(year_num), MONTHS[month_name.lower()], 1)
        if date(2026, 8, 1) <= d <= date(2027, 3, 1):
            candidates.append((d.month, d.year))
    if not candidates:
        return None
    # Prefer target-range month if several are in DOM.
    for month, year in candidates:
        if (year, month) in {(2026, 12), (2027, 1), (2027, 2)}:
            return month, year
    return candidates[0]


async def _click_next_month(frame) -> bool:
    selectors = [
        "button[aria-label*='next' i][aria-label*='month' i]",
        "button[title*='next' i][title*='month' i]",
        "[role='button'][aria-label*='next' i][aria-label*='month' i]",
        "a[aria-label*='next' i][aria-label*='month' i]",
    ]
    for selector in selectors:
        locator = frame.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for i in range(min(count, 4)):
            item = locator.nth(i)
            try:
                if await item.is_visible():
                    await item.click(timeout=2500)
                    await frame.page.wait_for_timeout(650)
                    return True
            except Exception:
                continue
    return False


async def _scan_calendar_days(frame, month: int, year: int) -> tuple[dict[str, set[str]], set[str]]:
    """Click likely date controls for a visible month and read time controls."""
    mapping: dict[str, set[str]] = defaultdict(set)
    seen_dates: set[str] = set()
    locator = frame.locator("button, [role='button']")
    try:
        count = await locator.count()
    except Exception:
        return mapping, seen_dates

    candidates = []
    for i in range(min(count, 500)):
        el = locator.nth(i)
        try:
            if not await el.is_visible():
                continue
            text = (await el.inner_text(timeout=400)).strip()
            aria = (await el.get_attribute("aria-label") or "").strip()
            title = (await el.get_attribute("title") or "").strip()
            data_date = (await el.get_attribute("data-date") or "").strip()
        except Exception:
            continue
        combined = " ".join(x for x in (text, aria, title, data_date) if x)
        explicit = parse_date_token(combined)
        day_num = None
        if explicit and explicit.year == year and explicit.month == month:
            day_num = explicit.day
        elif re.fullmatch(r"\d{1,2}", text):
            n = int(text)
            if 1 <= n <= 31:
                day_num = n
        if day_num is not None:
            candidates.append((day_num, el, bool(explicit)))

    # Numeric-only date buttons are only trusted when the frame looks like a calendar.
    numeric_count = sum(1 for _, _, explicit in candidates if not explicit)
    filtered = [c for c in candidates if c[2] or numeric_count >= 20]
    used_days = set()
    for day_num, el, _ in filtered:
        if day_num in used_days:
            continue
        used_days.add(day_num)
        try:
            d = date(year, month, day_num)
        except ValueError:
            continue
        if not in_target(d):
            continue
        key = d.isoformat()
        try:
            disabled = await el.is_disabled()
        except Exception:
            disabled = False
        if disabled:
            # Disabled dates can still be useful evidence that the date exists in the widget,
            # but are not treated as a newly discovered performance.
            continue
        try:
            await el.click(timeout=2200)
            await frame.page.wait_for_timeout(300)
        except Exception:
            continue
        times = await _interactive_times(frame)
        if times:
            mapping[key].update(times)
            seen_dates.add(key)
    return mapping, seen_dates


async def scrape_phantom_peak() -> dict:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - only relevant outside action
        raise RuntimeError("Playwright is not installed. Run: pip install playwright && playwright install chromium") from exc

    network_texts: list[str] = []
    diagnostics: list[str] = []
    mapping: dict[str, set[str]] = defaultdict(set)
    seen_dates: set[str] = set()
    cancelled_dates: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="en-GB",
            timezone_id="Europe/London",
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36 PPECChecker/2.0",
        )
        page = await context.new_page()

        async def capture_response(response):
            url = response.url.lower()
            ctype = (response.headers.get("content-type") or "").lower()
            interesting = any(token in url for token in ("ticket", "event", "performance", "schedule", "session", "slot", "availability", "flow", "stage"))
            if not interesting and "json" not in ctype:
                return
            try:
                text = await response.text()
            except Exception:
                return
            if len(text) <= 2_000_000:
                network_texts.append(text)

        page.on("response", lambda response: asyncio.create_task(capture_response(response)))
        await page.goto(PP_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(5500)

        # Trigger common lazy-loaded booking widgets.
        for fraction in (0.35, 0.7, 1.0):
            await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {fraction})")
            await page.wait_for_timeout(800)

        # Parse visible DOM and useful attributes before interacting.
        for frame in page.frames:
            snap = await _frame_snapshot(frame)
            body = snap["body"]
            if body:
                m, s, c = parse_date_time_lines(body)
                for key, times in m.items(): mapping[key].update(times)
                seen_dates.update(s); cancelled_dates.update(c)
            for item in snap["items"]:
                combined = " ".join(str(item.get(k, "")) for k in ("text", "aria", "title", "dataDate", "dataTime", "datetime"))
                d = parse_date_token(combined)
                if d and in_target(d):
                    key = d.isoformat(); seen_dates.add(key)
                    mapping[key].update(extract_times(combined))
                    if CANCEL_RE.search(combined): cancelled_dates.add(key)
            diagnostics.append(f"\n===== FRAME {snap['url']} =====\n{body[:30000]}")

        # If a calendar widget is present, walk target months and click its date controls.
        for _ in range(10):
            progressed = False
            for frame in page.frames:
                my = await _month_year(frame)
                if not my:
                    continue
                month, year = my
                current = date(year, month, 1)
                if current < date(2026, 12, 1):
                    if await _click_next_month(frame):
                        progressed = True
                    continue
                if current > date(2027, 2, 1):
                    continue
                m, s = await _scan_calendar_days(frame, month, year)
                for key, times in m.items(): mapping[key].update(times)
                seen_dates.update(s)
                if current < date(2027, 2, 1) and await _click_next_month(frame):
                    progressed = True
            if not progressed:
                break

        await page.wait_for_timeout(1200)
        try:
            await page.screenshot(path=str(DIAG_DIR / "phantom-peak-page.png"), full_page=True)
        except Exception:
            pass
        await browser.close()

    # Parse captured public network response bodies too; this often finds the cleanest data.
    for text in network_texts:
        m, s, c = parse_date_time_lines(text)
        for key, times in m.items(): mapping[key].update(times)
        seen_dates.update(s); cancelled_dates.update(c)

    # Drop unlikely times that are not close to the known PP start-time family.
    # We keep flexibility for future schedule changes but reject obvious midnight/system timestamps.
    for key in list(mapping):
        mapping[key] = {t for t in mapping[key] if "09:00" <= t <= "22:30"}
        if not mapping[key]:
            del mapping[key]

    discovered_dates = sorted(mapping)
    successful = (
        len(discovered_dates) >= 20
        and discovered_dates[0] <= "2026-12-20"
        and discovered_dates[-1] >= "2027-02-15"
    )

    DIAG_DIR.mkdir(exist_ok=True)
    (DIAG_DIR / "phantom-peak-scan.txt").write_text(
        "PPEC Phantom Peak scan diagnostics\n"
        f"Successful full-range scan: {successful}\n"
        f"Dates with times: {len(discovered_dates)}\n"
        f"First/last: {discovered_dates[0] if discovered_dates else '-'} / {discovered_dates[-1] if discovered_dates else '-'}\n"
        f"Seen dates: {len(seen_dates)}\n"
        f"Network bodies inspected: {len(network_texts)}\n"
        + "\nDISCOVERED\n"
        + "\n".join(f"{d}: {', '.join(sorted(mapping[d]))}" for d in discovered_dates)
        + "\n\nDOM SNAPSHOTS\n"
        + "\n".join(diagnostics),
        encoding="utf-8",
    )

    return {
        "successful": successful,
        "times": {key: sorted(values) for key, values in mapping.items()},
        "seenDates": sorted(seen_dates),
        "cancelledDates": sorted(cancelled_dates),
    }


def merge_phantom_peak(data: dict, scan: dict) -> bool:
    pp = data["phantomPeak"]
    pp["lastChecked"] = today_label()
    if not scan["successful"]:
        pp["liveSyncStatus"] = "scan-failed-fallback-retained"
        return False

    pp["liveSyncStatus"] = "live"
    pp["lastSuccessfulLiveSync"] = today_label()
    discovered = scan["times"]
    seen_dates = set(scan["seenDates"])
    cancelled = set(scan["cancelledDates"])
    existing = {p["date"]: p for p in pp["performances"]}

    for key, times in discovered.items():
        if key in existing:
            perf = existing[key]
            if times:
                perf["times"] = times
            perf["basis"] = "live-ticketing"
            perf["listingStatus"] = "normal"
            perf["missingLiveRuns"] = 0
            if key not in cancelled:
                perf["status"] = "scheduled"
        else:
            perf = {
                "date": key,
                "times": times,
                "note": None,
                "basis": "live-ticketing",
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
        if key in seen_dates or key in discovered:
            perf["missingLiveRuns"] = 0
            if perf.get("status") != "cancelled":
                perf["listingStatus"] = "normal"
            continue
        perf["missingLiveRuns"] = int(perf.get("missingLiveRuns", 0)) + 1
        if perf["missingLiveRuns"] >= 2:
            perf["listingStatus"] = "review"

    pp["performances"].sort(key=lambda p: p["date"])
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stadium-only", action="store_true")
    parser.add_argument("--phantom-only", action="store_true")
    args = parser.parse_args()
    if args.stadium_only and args.phantom_only:
        raise SystemExit("Choose only one of --stadium-only or --phantom-only")

    data = load_data()
    errors: list[str] = []

    if not args.phantom_only:
        try:
            events = scrape_stadium()
            data["londonStadium"]["events"] = events
            data["londonStadium"]["lastChecked"] = today_label()
            print(f"London Stadium: {len(events)} events in target range")
        except Exception as exc:
            errors.append(f"London Stadium refresh failed: {exc}")
            print(errors[-1])

    if not args.stadium_only:
        try:
            scan = asyncio.run(scrape_phantom_peak())
            live = merge_phantom_peak(data, scan)
            if live:
                print(f"Phantom Peak: live full-range sync succeeded ({len(scan['times'])} dates with times)")
            else:
                print("Phantom Peak: full-range live sync not verified; fallback retained (diagnostic artifact written)")
        except Exception as exc:
            data["phantomPeak"]["lastChecked"] = today_label()
            data["phantomPeak"]["liveSyncStatus"] = "scan-error-fallback-retained"
            DIAG_DIR.mkdir(exist_ok=True)
            (DIAG_DIR / "phantom-peak-error.txt").write_text(str(exc) + "\n", encoding="utf-8")
            errors.append(f"Phantom Peak refresh failed: {exc}")
            print(errors[-1])

    save_data(data)
    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()
