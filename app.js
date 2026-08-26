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

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function timeLabel(time) {
    return `<span class="time-pill">${esc(time)}</span>`;
  }

  function performanceTimesMarkup(performance) {
    if (performance.times?.length) {
      return `<div class="show-times">${performance.times.map(timeLabel).join('')}</div>`;
    }

    if (performance.note) {
      return `<div class="show-times"><span class="time-pill special">Special event</span><span class="time-pill muted">Time TBC</span></div>`;
    }

    return '<div class="show-times"><span class="time-pill muted">Time TBC</span></div>';
  }

  function statusBadge(events) {
    if (events.length) return '<span class="slot-tag clash">Busy Stratford</span>';
    return '<span class="slot-tag clear">Clear day</span>';
  }

  function stadiumMarkup(events) {
    if (!events.length) {
      return `
        <div class="no-stadium">
          <span class="state-icon">✓</span>
          <div>
            <strong>No Stadium event</strong>
            <span>Nothing currently listed for London Stadium on this date.</span>
          </div>
        </div>`;
    }

    return `
      <div class="stadium-event">
        <div class="status-label">London Stadium event</div>
        <div class="stadium-list">${events.map(event => `
          <div class="stadium-item">
            <span class="state-icon">!</span>
            <div class="stadium-item-copy">
              <div class="stadium-name">${esc(event.name)}</div>
              <div class="stadium-time">Start time: ${event.time ? esc(event.time) : 'Time TBC'}</div>
            </div>
          </div>`).join('')}
        </div>
      </div>`;
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

    const clashDays = new Set(clashes.map(item => item.performance.date)).size;
    if (!performances.length) {
      summary.className = 'summary-card empty';
      summary.innerHTML = `
        <div class="summary-main"><span class="summary-dot"></span><span>No Phantom Peak performances currently listed this week.</span></div>
        <p class="summary-sub">Use Previous or Next to browse another week.</p>`;
    } else if (clashDays) {
      summary.className = 'summary-card clash';
      summary.innerHTML = `
        <div class="summary-main"><span class="summary-dot"></span><span><strong>${clashDays} busy ${clashDays === 1 ? 'day' : 'days'}</strong> this week — allow extra Stratford travel time.</span></div>
        <p class="summary-sub">Each highlighted date shows the Stadium event name and start time, or Time TBC if it has not been published yet.</p>`;
    } else {
      summary.className = 'summary-card safe';
      summary.innerHTML = `
        <div class="summary-main"><span class="summary-dot"></span><span><strong>Clear week:</strong> no same-day London Stadium events currently listed.</span></div>`;
    }

    schedule.replaceChildren();
    schedule.dataset.count = String(performances.length);

    if (!performances.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-week';
      empty.textContent = 'Use Previous or Next to browse another week.';
      schedule.appendChild(empty);
      return;
    }

    performances.forEach(performance => {
      const parts = dateParts(performance.date);
      const events = stadiumFor(performance.date);
      const needsReview = performance.listingStatus === 'review';
      const row = document.createElement('article');
      row.className = `day-card${events.length ? ' has-clash' : ''}${needsReview ? ' needs-review' : ''}`;

      row.innerHTML = `
        <div class="date-block">
          <span class="date-weekday">${esc(parts.weekday)}</span>
          <strong class="date-day">${esc(parts.day)}</strong>
          <span class="date-month">${esc(parts.month)}</span>
        </div>
        <div class="day-content">
          <div class="day-heading-row">
            <h3 class="day-title">Phantom Peak</h3>
            ${statusBadge(events)}
            <span class="source-badge ${needsReview ? 'review' : 'live'}">${needsReview ? 'Check advised' : 'Performance listed'}</span>
          </div>
          ${performanceTimesMarkup(performance)}
          ${performance.note ? `<div class="special-note">${esc(performance.note)}</div>` : ''}
          ${needsReview ? '<div class="review-note">This performance disappeared from a recent live check and should be confirmed on the official ticket page.</div>' : ''}
          ${stadiumMarkup(events)}
        </div>`;

      schedule.appendChild(row);
    });
  }

  prevButton.addEventListener('click', () => {
    if (weekIndex > 0) {
      weekIndex -= 1;
      render();
    }
  });

  nextButton.addEventListener('click', () => {
    if (weekIndex < weeks.length - 1) {
      weekIndex += 1;
      render();
    }
  });

  ppChecked.textContent = `Phantom Peak: ${data.phantomPeak.lastChecked || 'seed data'}`;
  stadiumChecked.textContent = `London Stadium: ${data.londonStadium.lastChecked || 'seed data'}`;
  render();
})();
