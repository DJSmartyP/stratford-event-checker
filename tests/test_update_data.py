import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts import update_data as updater


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.data = updater.load_data()
        self.now = datetime.now(timezone.utc)
        updater.initialise_refresh_metadata(self.data, self.now)
        for key in updater.SOURCE_NAMES:
            self.data[key]['refresh']['lastSuccessfulRefreshAt'] = self.now.isoformat()
        self.original = copy.deepcopy(self.data)

    def scan(self, successful=True):
        return dict(successful=successful, times={'2026-12-04': ['19:00']},
                    availableDates=['2026-12-04'], seenDates=[], cancelledDates=[],
                    scannedMonths=['2026-12', '2027-01', '2027-02'])

    @patch.object(updater.time, 'sleep')
    @patch.object(updater.requests, 'get')
    def test_timeout_retries_three_times(self, get, sleep):
        get.side_effect = updater.requests.ConnectTimeout('temporary outage')
        with self.assertRaises(updater.requests.ConnectTimeout):
            updater.scrape_stadium()
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    @patch.object(updater.time, 'sleep')
    @patch.object(updater.requests, 'get')
    def test_request_recovers_on_third_attempt(self, get, sleep):
        response = Mock(text='<p>4 December 2026</p><p>Test event</p>')
        get.side_effect = [updater.requests.Timeout(), updater.requests.Timeout(), response]
        self.assertEqual(updater.scrape_stadium()[0]['name'], 'Test event')
        self.assertEqual(get.call_count, 3)

    @patch.object(updater.requests, 'get')
    def test_empty_stadium_page_is_rejected(self, get):
        get.return_value = Mock(text='<html>Temporarily unavailable</html>')
        with self.assertRaises(RuntimeError):
            updater.scrape_stadium()

    def test_stadium_outage_does_not_block_pp(self):
        with patch.object(updater, 'scrape_stadium', side_effect=updater.requests.Timeout()), patch.object(
            updater, 'scrape_phantom_peak', new=AsyncMock(return_value=self.scan())
        ):
            updater.refresh_sources(self.data)
        self.assertEqual(self.data['londonStadium']['events'], self.original['londonStadium']['events'])
        self.assertEqual(self.data['londonStadium']['refresh']['lastSuccessfulRefreshAt'], self.now.isoformat())
        self.assertEqual(self.data['phantomPeak']['performances'][0]['times'], ['19:00'])
        self.assertEqual(updater.stale_sources(self.data, self.now), [])

    def test_incomplete_pp_scan_preserves_all_performance_fields(self):
        events = [{'date': '2026-12-04', 'name': 'Updated', 'time': None, 'status': 'scheduled'}]
        with patch.object(updater, 'scrape_stadium', return_value=events), patch.object(
            updater, 'scrape_phantom_peak', new=AsyncMock(return_value=self.scan(False))
        ):
            updater.refresh_sources(self.data)
        self.assertEqual(self.data['londonStadium']['events'], events)
        self.assertEqual(self.data['phantomPeak']['performances'], self.original['phantomPeak']['performances'])
        self.assertEqual(self.data['phantomPeak']['liveSyncStatus'], 'scan-failed-fallback-retained')
        self.assertEqual(self.data['phantomPeak']['refresh']['lastSuccessfulRefreshAt'], self.now.isoformat())

    def test_partial_merge_exception_rolls_back(self):
        def broken_merge(data, scan):
            data['phantomPeak']['performances'].clear()
            raise RuntimeError('merge failed')
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, 'DIAG_DIR', Path(tmp)), patch.object(
            updater, 'scrape_phantom_peak', new=AsyncMock(return_value=self.scan())
        ), patch.object(updater, 'merge_phantom_peak', side_effect=broken_merge):
            updater.refresh_sources(self.data, phantom_only=True)
        self.assertEqual(self.data['phantomPeak']['performances'], self.original['phantomPeak']['performances'])
        self.assertEqual(self.data['phantomPeak']['liveSyncStatus'], 'scan-error-fallback-retained')

    def test_each_source_has_48_hour_boundary(self):
        for key, name in updater.SOURCE_NAMES.items():
            data = copy.deepcopy(self.data)
            data[key]['refresh']['lastSuccessfulRefreshAt'] = (self.now - timedelta(hours=48)).isoformat()
            self.assertEqual(updater.stale_sources(data, self.now - timedelta(microseconds=1)), [])
            self.assertEqual(updater.stale_sources(data, self.now), [name])

    def test_legacy_migration_does_not_invent_pp_success_or_reset_grace(self):
        data = copy.deepcopy(self.original)
        for key in updater.SOURCE_NAMES:
            del data[key]['refresh']
        data['phantomPeak']['lastSuccessfulLiveSync'] = None
        updater.initialise_refresh_metadata(data, self.now)
        self.assertIsNone(data['phantomPeak']['refresh']['lastSuccessfulRefreshAt'])
        self.assertIsNotNone(data['londonStadium']['refresh']['lastSuccessfulRefreshAt'])
        updater.initialise_refresh_metadata(data, self.now + timedelta(hours=47))
        self.assertEqual(data['phantomPeak']['refresh']['monitoringStartedAt'], self.now.isoformat())
        self.assertIn('Phantom Peak', updater.stale_sources(data, self.now + timedelta(hours=48)))

    def test_stale_failure_saves_other_source_and_status_first(self):
        self.data['londonStadium']['refresh']['lastSuccessfulRefreshAt'] = (self.now - timedelta(hours=49)).isoformat()
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, 'DATA_FILE', Path(tmp) / 'schedule-data.js'):
            updater.save_data(self.data)
            with patch('sys.argv', ['update_data.py']), patch.object(
                updater, 'scrape_stadium', side_effect=updater.requests.Timeout()
            ), patch.object(updater, 'scrape_phantom_peak', new=AsyncMock(return_value=self.scan())):
                with self.assertRaisesRegex(SystemExit, 'London Stadium'):
                    updater.main()
            saved = updater.load_data()
            self.assertEqual(saved['phantomPeak']['performances'][0]['times'], ['19:00'])
            self.assertEqual(saved['londonStadium']['events'], self.original['londonStadium']['events'])
            self.assertEqual(saved['londonStadium']['refresh']['status'], 'error-fallback-retained')

    def test_both_outages_defer_failure_until_after_save(self):
        for key in updater.SOURCE_NAMES:
            self.data[key]['refresh']['lastSuccessfulRefreshAt'] = (self.now - timedelta(hours=49)).isoformat()
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, 'DATA_FILE', Path(tmp) / 'schedule-data.js'), patch.object(updater, 'DIAG_DIR', Path(tmp)):
            updater.save_data(self.data)
            with patch('sys.argv', ['update_data.py', '--defer-freshness-check']), patch.object(
                updater, 'scrape_stadium', side_effect=updater.requests.Timeout()
            ), patch.object(updater, 'scrape_phantom_peak', new=AsyncMock(side_effect=RuntimeError('offline'))):
                updater.main()
            saved = updater.load_data()
            self.assertEqual(saved['phantomPeak']['performances'], self.original['phantomPeak']['performances'])
            self.assertEqual(saved['londonStadium']['events'], self.original['londonStadium']['events'])
            before = updater.DATA_FILE.read_bytes()
            with patch('sys.argv', ['update_data.py', '--check-freshness']), patch.object(updater, 'scrape_stadium') as scrape:
                with self.assertRaisesRegex(SystemExit, 'London Stadium, Phantom Peak'):
                    updater.main()
                scrape.assert_not_called()
            self.assertEqual(updater.DATA_FILE.read_bytes(), before)

    def test_missing_performances_are_retained_and_flagged_only_after_two_scans(self):
        scan = self.scan()
        updater.merge_phantom_peak(self.data, scan)
        missing = self.data['phantomPeak']['performances'][1]
        self.assertEqual(missing['missingLiveRuns'], 1)
        updater.merge_phantom_peak(self.data, scan)
        self.assertEqual(missing['listingStatus'], 'review')
        self.assertEqual(len(self.data['phantomPeak']['performances']), len(self.original['phantomPeak']['performances']))


if __name__ == '__main__':
    unittest.main()
