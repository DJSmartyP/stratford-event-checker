import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import update_data as updater


class StadiumBackupTests(unittest.TestCase):
    def setUp(self):
        self.rows = json.loads((Path(__file__).parent / 'fixtures/whu-fixtures.json').read_text())

    def html(self):
        stream = '1c:' + json.dumps(self.rows) + '\n'
        # Real transport can split a JSON record across script elements.
        parts = [stream[:120], stream[120:]]
        return ''.join('<script>self.__next_f.push(' + json.dumps([1, p]) + ')</script>' for p in parts)

    def test_official_fixture_inventory_and_moved_match(self):
        events = updater.parse_west_ham_html(self.html())
        self.assertEqual(len(events), 8)
        millwall = next(e for e in events if e['name'].endswith('Millwall'))
        self.assertEqual((millwall['date'], millwall['time']), ('2027-02-21', '12:00'))
        self.assertTrue(all(e['name'].startswith('West Ham United v ') for e in events))

    def test_missing_month_is_rejected(self):
        self.rows = [r for r in self.rows if not r['kickOffUtc'].startswith('2027-02')]
        with self.assertRaises(ValueError):
            updater.parse_west_ham_html(self.html())

    def test_tbc_and_cancellation_are_explicit(self):
        row = next(r for r in self.rows if r['kickOffUtc'].startswith('2026-12-08'))
        row['kickOffTbc'] = True
        self.assertIsNone(updater.parse_west_ham_html(self.html())[0]['time'])
        row['fixtureProps']['matchStatus'] = 'Cancelled'
        self.assertEqual(updater.parse_west_ham_html(self.html())[0]['status'], 'cancelled')

    def test_merge_retains_concert_and_absent_fixture_and_updates_date(self):
        events = updater.parse_west_ham_html(self.html())
        source = {'events': [
            {'date': '2027-02-20', 'time': '12:00', 'name': 'West Ham United v Millwall', 'status': 'scheduled'},
            {'date': '2027-02-01', 'time': None, 'name': 'Concert', 'status': 'scheduled'},
            {'date': '2027-02-02', 'time': None, 'name': 'West Ham United v Missing', 'status': 'scheduled'},
        ]}
        updater.merge_west_ham(source, events)
        self.assertEqual(len(source['events']), 10)
        self.assertEqual(next(e['date'] for e in source['events'] if e['name'].endswith('Millwall')), '2027-02-21')
        self.assertTrue(any(e['name'] == 'Concert' for e in source['events']))
        updater.merge_west_ham(source, events)
        self.assertEqual(len(source['events']), 10)

    def test_partial_refresh_does_not_reset_full_venue_staleness(self):
        data = updater.load_data()
        now = datetime.now(timezone.utc)
        updater.initialise_refresh_metadata(data, now)
        previous = (now - timedelta(hours=49)).isoformat()
        data['londonStadium']['refresh']['lastSuccessfulRefreshAt'] = previous
        with patch.object(updater, 'scrape_stadium', side_effect=updater.requests.Timeout()), patch.object(
            updater, 'scrape_west_ham', return_value=updater.parse_west_ham_html(self.html())
        ):
            updater.refresh_sources(data, stadium_only=True)
        stadium = data['londonStadium']
        self.assertEqual(stadium['refresh']['status'], 'partial-football-refresh')
        self.assertEqual(stadium['refresh']['lastSuccessfulRefreshAt'], previous)
        self.assertEqual(stadium['footballRefresh']['status'], 'success')
        self.assertIn('London Stadium', updater.stale_sources(data, now))

    def test_ambiguous_merge_rolls_back_entire_backup(self):
        source = {'events': [{'name': 'West Ham United v Millwall', 'date': '2027-02-20'}] * 2,
                  'refresh': {'status': 'error-fallback-retained'}}
        before = copy.deepcopy(source['events'])
        with patch.object(updater, 'scrape_west_ham', return_value=updater.parse_west_ham_html(self.html())):
            updater.refresh_stadium_backup(source)
        self.assertEqual(source['events'], before)
        self.assertEqual(source['footballRefresh']['status'], 'error-fallback-retained')

    def test_error_page_is_rejected(self):
        with self.assertRaises(ValueError):
            updater.parse_west_ham_html('<html>Unavailable</html>')
