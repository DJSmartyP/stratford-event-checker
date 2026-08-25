(() => {
  'use strict';

  const data = window.CALENDAR_DATA;
  if (!data) return;

  const RANGE_START = data.range.start;
  const RANGE_END = data.range.end;
  const prevButton = document.getElementById('prevWeek');
  const nextButton = document.getElementById('nextWeek');
  const weekLabel = document.getElementById('weekLabel');
  const weekRange = document.getElementById('weekRange');
  const weekCounter = document.getElementById('weekCounter');
  const summary = document.getElementById('summary');
  const schedule = document.getElementById('schedule');
  const ppChecked = document.getElementById('ppChecked');
  const stadiumChecked = document.getElementById('stadiumChecked');

  function parseDate(dateString) {
    const [year, month, day] = dateString.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  }

  function isoDate(date) {
    return date.toISOString().slice(0, 10);
  }

  function addDays(date, days) {
    const copy = new Date(date.getTime());
    copy.setUTCDate(copy.getUTCDate() + days);
    return copy;
  }

  function mondayOf(date) {
    const weekday = date.getUTCDay();
    const diff = weekday === 0 ? -6 : 1 - weekday;
    return addDays(date, diff);
  }

  function buildWeeks() {
    const firstMonday = mondayOf(parseDate(RANGE_START));
    const end = parseDate(RANGE_END);
    const result = [];
    let start = firstMonday;
    while (start <= end) {
      result.push({ start, end: addDays(start, 6) });
      start = addDays(start, 7);
    }
    return result;
  }

  const weeks = buildWeeks();
  let weekIndex = 0;

  const dayFormatter = new Intl.DateTimeFormat('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', timeZone: 'UTC'
  });
  const rangeFormatter = new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'
  });

  function dateParts(dateString) {
    const parts = dayFormatter.formatToParts(parseDate(dateString));
    return Object.fromEntries(parts.map(part => [part.type, part.value]));
  }

  function rangeText(start, end) {
    const s = rangeFormatter.format(start);
    const e = rangeFormatter.format(end);
    if (start.getUTCFullYear() === end.getUTCFullYear()) {
      const sameMonth = start.getUTCMonth() === end.getUTCMonth();
      if (sameMonth) {
        const month = start.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' });
        return `${start.getUTCDate()}–${end.getUTCDate()} ${month} ${end.getUTCFullYear()}`.toUpperCase();
      }
      const left = start.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
      return `${left} – ${e}`.toUpperCase();
    }
    return `${s} – ${e}`.toUpperCase();
  }

  function performancesForWeek(week) {
    return data.phantomPeak.performances
      .filter(p => p.date >= isoDate(week.start) && p.date <= isoDate(week.end) && p.status !== 'cancelled')
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  function stadiumFor(date) {
    return data.londonStadium.events.filter(event => event.date === date && event.status !== 'cancelled');
  }

  function basisBadge(performance) {
    if (performance.listingStatus === 'review') {
      return '<span class="source-badge review">Check advised</span>';
    }
    if (performance.basis === 'live-ticketing') {
      return '<span class="source-badge live">Live listing</span>';
    }
    if (performance.basis === 'official-opening-schedule') {
      return '<span class="source-badge">Published schedule</span>';
    }
    return '<span class="source-badge">Regular pattern</span>';
  }

  function render() {
    const week = weeks[weekIndex];
    const performances = performancesForWeek(week);
    const clashes = performances.flatMap(performance =>
      stadiumFor(performance.date).map(event => ({ performance, event }))
    );

    weekLabel.textContent = weekIndex === 0 ? 'Opening week' : `Week ${weekIndex + 1}`;
    weekRange.textContent = rangeText(week.start, week.end);
    weekCounter.textContent = `Week ${weekIndex + 1} of ${weeks.length}`;

    prevButton.hidden = weekIndex === 0;
    nextButton.hidden = weekIndex === weeks.length - 1;

    if (!performances.length) {
      summary.className = 'summary-card empty';
      summary.innerHTML = '<div class="summary-main"><span class="summary-dot"></span><span>No Phantom Peak performances are currently listed this week</span></div>';
    } else if (clashes.length) {
      const days = new Set(clashes.map(item => item.performance.date)).size;
      summary.className = 'summary-card clash';
      summary.innerHTML = `<div class="summary-main"><span class="summary-dot"></span><span>${days} Phantom Peak ${days === 1 ? 'day has' : 'days have'} a same-day London Stadium event</span></div><p class="summary-sub">The affected ${days === 1 ? 'day is' : 'days are'} highlighted below. Expect heavier crowds and transport demand around Stratford.</p>`;
    } else {
      summary.className = 'summary-card safe';
      summary.innerHTML = '<div class="summary-main"><span class="summary-dot"></span><span>No London Stadium event currently coincides with a Phantom Peak performance this week</span></div>';
    }

    schedule.replaceChildren();

    if (!performances.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-week';
      empty.textContent = 'Nothing to compare this week yet. Use Previous week or Next week to continue browsing.';
      schedule.appendChild(empty);
      return;
    }

    performances.forEach(performance => {
      const parts = dateParts(performance.date);
      const stadiumEvents = stadiumFor(performance.date);
      const needsReview = performance.listingStatus === 'review';
      const card = document.createElement('article');
      card.className = `day-card${stadiumEvents.length ? ' has-clash' : ''}${needsReview ? ' needs-review' : ''}`;

      const times = performance.times?.length
        ? `<div class="show-times">${performance.times.map(time => `<span class="time-pill">${time}</span>`).join('')}</div>`
        : '';

      const special = performance.note ? `<div class="special-note">${performance.note}</div>` : '';
      const review = needsReview
        ? '<div class="review-note"><strong>Ticket listing changed.</strong> This performance is being retained because a missing sales slot can mean sold out rather than cancelled. Check the official ticket page before relying on the time.</div>'
        : '';

      const stadium = stadiumEvents.length
        ? stadiumEvents.map(event => `
          <div class="stadium-event">
            <div class="status-label">! London Stadium same day</div>
            <div class="stadium-name">${event.name}</div>
            <div class="stadium-time">${event.time ? `Starts ${event.time}` : 'Time TBC'}</div>
          </div>`).join('')
        : '<div class="no-stadium">✓ No London Stadium event listed for this date</div>';

      card.innerHTML = `
        <div class="date-block">
          <div class="date-day">${parts.day}</div>
          <div class="date-month">${parts.month}</div>
          <div class="date-weekday">${parts.weekday}</div>
        </div>
        <div class="day-content">
          <div class="day-heading-row">
            <h3 class="day-title">Phantom Peak</h3>
            ${basisBadge(performance)}
          </div>
          ${times}
          ${special}
          ${review}
          ${stadium}
        </div>`;

      schedule.appendChild(card);
    });
  }

  prevButton.addEventListener('click', () => {
    if (weekIndex > 0) {
      weekIndex -= 1;
      render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  nextButton.addEventListener('click', () => {
    if (weekIndex < weeks.length - 1) {
      weekIndex += 1;
      render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  ppChecked.textContent = `Phantom Peak schedule checked: ${data.phantomPeak.lastChecked || 'not yet live-synced'}.`;
  stadiumChecked.textContent = `London Stadium checked: ${data.londonStadium.lastChecked || 'seed data'}.`;
  render();
})();
