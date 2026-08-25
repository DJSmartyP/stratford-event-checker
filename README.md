# PPEC Stratford Crowd Checker

A lightweight static website for comparing Phantom Peak performance days with events at London Stadium.

The site covers **4 December 2026 to 28 February 2027** and opens on Phantom Peak's opening week. Visitors can use **Next week** and **Previous week** to move through all 13 calendar weeks.

## What it does

- Shows Phantom Peak performance dates and start times.
- Flags any London Stadium event on the same calendar date.
- Shows the Stadium event name and advertised start time. If no time has been published yet, it shows **Time TBC** rather than ignoring the event.
- Uses the supplied PPEC badge and a Phantom Peak-inspired visual style.
- Works as a static GitHub Pages site: no database, server or paid hosting is required.
- A GitHub Action checks both public calendars every six hours.

## Automatic updates

### London Stadium

The action reads the official London Stadium events page:

`https://www.london-stadium.com/events/all.html`

A successful scan replaces the Stadium events inside the configured Dec-Feb window. **Any dated event is retained even if its time is still TBC.** This means additions, removals, names and advertised times can update automatically; a Time TBC listing will gain its start time on a later scan as soon as London Stadium publishes it.

### Phantom Peak

The action opens Phantom Peak's public ticket page using a headless Chromium browser (Playwright):

`https://www.phantompeak.com/tickets/?flow=lyTxE9UF`

It looks for the public performance calendar in the page, embedded frames and booking-related network responses.

The update policy is intentionally cautious:

- **New performance/date found:** add it automatically.
- **Existing date has a different listed time:** update the time automatically.
- **Page explicitly says a date is cancelled:** mark it cancelled.
- **A known performance simply disappears from ticket sales:** do **not** delete it. A missing sales slot may mean *sold out*, not cancelled. After two successful full-range scans where it is absent, the website marks that date **Check advised** while retaining it for crowd-planning purposes.

That last rule avoids the dangerous result where a sold-out Phantom Peak performance is treated as if no crowd will be present.

## Initial schedule data

The ZIP contains seed data so the site is useful immediately:

- Phantom Peak's published opening schedule is included through 3 January.
- January/February initially use Phantom Peak's published regular show-time pattern (Friday 18:00, Saturday 12:00 & 18:00, Sunday 12:00) as a fallback until the live ticket sync verifies those dates.
- London Stadium events are seeded from the official events calendar as checked on 25 August 2026.

Once the first successful live Phantom Peak scan runs, verified dates are labelled **Live listing** on the site.

# Install on GitHub Pages

## 1. Create the repository

Create a new GitHub repository. Public is simplest for GitHub Pages, although your GitHub plan may also support Pages on a private repository.

## 2. Upload the ZIP contents

Upload **the contents of this folder**, not the outer folder itself. The root of the repository should contain:

- `index.html`
- `styles.css`
- `app.js`
- `schedule-data.js`
- `requirements.txt`
- `assets/`
- `scripts/`
- `.github/`

**Important:** make sure the hidden `.github` folder is included. That folder contains the automatic update workflow.

## 3. Turn on GitHub Pages

Go to:

**Repository → Settings → Pages**

Under **Build and deployment** choose:

- Source: **Deploy from a branch**
- Branch: **main**
- Folder: **/ (root)**

Save. GitHub will display the site's public URL once deployment completes.

## 4. Allow the updater to write schedule changes

Go to:

**Repository → Settings → Actions → General → Workflow permissions**

Select:

**Read and write permissions**

Save.

If you have branch protection enabled on `main`, GitHub Actions must also be permitted to push to that branch, or the automatic updater will be unable to save refreshed data.

## 5. Run the first calendar check

Go to:

**Repository → Actions → Refresh Stratford calendars → Run workflow**

Run it once manually. After that, GitHub schedules it automatically every six hours.

The first Playwright run can take several minutes because GitHub installs a Chromium browser in the Action environment.

# Checking whether the Phantom Peak auto-reader worked

Open the completed workflow run in **Actions**.

A successful full-range scan logs something similar to:

`Phantom Peak: live full-range sync succeeded`

If the Phantom Peak booking interface changes and the scraper cannot confidently read the complete Dec-Feb calendar, it **keeps the existing fallback data instead of wiping it**.

The workflow also uploads a temporary artifact called:

`phantom-peak-scan-diagnostics`

That artifact contains a public-page text snapshot and screenshot that can be used to adjust the reader if On The Stage or Phantom Peak change the booking interface. It expires after seven days and is not published on the website.

# Editing dates manually

The public data is stored in `schedule-data.js`.

You can edit it directly in GitHub if necessary. Each Phantom Peak entry looks like:

```json
{
  "date": "2027-01-16",
  "times": ["12:00", "18:00"],
  "note": null,
  "basis": "live-ticketing",
  "status": "scheduled",
  "listingStatus": "normal",
  "missingLiveRuns": 0
}
```

Do not change the outer `window.CALENDAR_DATA =` wrapper unless you also change `app.js`.

# Files

- `index.html` — page structure.
- `styles.css` — PPEC / Phantom Peak-inspired styling.
- `app.js` — 13-week browser and date comparison logic.
- `schedule-data.js` — currently published calendar data.
- `scripts/update_data.py` — both automatic readers and safety logic.
- `.github/workflows/refresh-calendar.yml` — runs the automatic check every six hours.
- `assets/ppec-logo.webp` — web-optimised version of the supplied PPEC badge.

# Notes

This is an independent planning tool and is not an official Phantom Peak or London Stadium service. Events can change at short notice, so users should still check their ticket and official venue information before travelling.

## Display layout
The site uses a single-page week view inside the illustrated parchment panel. There is no internal content scrolling: Previous/Next changes the whole week, and the busiest six-date week is automatically compressed to fit the panel on phone and laptop layouts.


### PPEC styling
The site uses the supplied PPEC badge, Phantom Peak-inspired purple/gold/cream colours and vintage explorer/showbill-style web fonts. It is a normal responsive webpage; no decorative background frame is required.
