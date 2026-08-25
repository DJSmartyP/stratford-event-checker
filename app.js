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

  function timeText(performance) {
    if (performance.times?.length) return performance.times.join(' · ');
    if (performance.note) return 'Special event · Time TBC';
    return 'Time TBC';
  }

  function stadiumMarkup(events) {
    if (!events.length) {
      return '<div class="stadium-clear"><span class="state-icon">✓</span><span>No Stadium event</span></div>';
    }

    return `<div class="stadium-clashes">${events.map(event => `
      <div class="stadium-clash">
        <span class="state-icon">!</span>
        <span class="stadium-copy"><strong>${esc(event.name)}</strong><span>${event.time ? esc(event.time) : 'Time TBC'}</span></span>
      </div>`).join('')}</div>`;
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
      summary.className = 'summary-card neutral';
      summary.innerHTML = '<span class="summary-icon">•</span><span>No Phantom Peak performances currently listed this week.</span>';
    } else if (clashDays) {
      summary.className = 'summary-card busy';
      summary.innerHTML = `<span class="summary-icon">!</span><span><strong>${clashDays} busy ${clashDays === 1 ? 'day' : 'days'}</strong> this week — allow extra Stratford travel time.</span>`;
    } else {
      summary.className = 'summary-card clear';
      summary.innerHTML = '<span class="summary-icon">✓</span><span><strong>Clear week:</strong> no same-day London Stadium events currently listed.</span>';
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
      row.className = `day-row${events.length ? ' has-clash' : ''}${needsReview ? ' needs-review' : ''}`;

      row.innerHTML = `
        <div class="date-chip">
          <span class="date-weekday">${esc(parts.weekday)}</span>
          <strong>${esc(parts.day)}</strong>
          <span>${esc(parts.month)}</span>
        </div>
        <div class="pp-slot">
          <div class="pp-name">Phantom Peak${needsReview ? ' <span class="review-mark" title="Ticket listing needs checking">?</span>' : ''}</div>
          <div class="pp-time">${esc(timeText(performance))}</div>
          ${performance.note ? `<div class="pp-note">${esc(performance.note)}</div>` : ''}
        </div>
        <div class="stadium-slot">${stadiumMarkup(events)}</div>`;

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

  const checkedText = data.londonStadium.lastChecked || 'seed data';
  stadiumChecked.textContent = `Stadium: ${checkedText}`;
  render();
})();
