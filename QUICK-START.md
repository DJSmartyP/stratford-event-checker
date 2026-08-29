# Quick start

1. Extract this ZIP.
2. Create a GitHub repository.
3. Upload **everything inside the extracted folder**, including `.github`.
4. In **Settings → Pages**, choose **Deploy from a branch → main → / (root)**.
5. In **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**.
6. Open **Actions → Refresh Stratford calendars → Run workflow** once.
7. Wait for GitHub Pages to publish the site URL.

After that the calendar check runs automatically every six hours.

If Phantom Peak's ticket interface cannot be read confidently, the site keeps its existing schedule rather than deleting dates. The Action run will contain a `phantom-peak-scan-diagnostics` artifact that can be used to adjust the reader.

Full details are in `README.md`.


## Stadium events with no published time

A dated London Stadium event is still shown as a clash even when the venue has not published a start time. It displays **Time TBC** until a later automatic refresh finds the time.

## Display layout
The site uses a single-page week view inside the illustrated parchment panel. There is no internal content scrolling: Previous/Next changes the whole week, and the busiest six-date week is automatically compressed to fit the panel on phone and laptop layouts.


**Status note:** the footer dates are generated from `schedule-data.js` and update after the refresh workflow runs. If a newly uploaded bundle briefly shows an older seeded date, the push-triggered refresh should replace it automatically.
