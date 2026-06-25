# Changelog

All notable changes to Pacebird are documented here.

## [Unreleased]

## [0.3.0] – 2024-06
### Added
- Swimming tab — monthly distance, session stats
- Achievements split into sport categories: Running, Cycling, Swimming, Total km, Pace, Duration, Streaks
- Cycling achievements (single-ride milestones + cumulative totals)
- Swimming achievements (First Swim → 10K total)
- Activity detail popup — click any row or PR card to see Leaflet map + splits
- Clickable PR cards on Running and Cycling tabs — open activity detail modal
- This Week vs Last Week comparison table (inline layout)
- Streak warning badge — shows "train today!" in amber when streak is at risk but still alive
- Synthetic swimming data in demo mode

### Changed
- Monthly distance charts are now responsive (no horizontal scroll)
- Cycling speed chart moved to full-width row
- Pace Improved metric now compares first vs last 10% of runs by average — more stable
- Current streak counts from yesterday if no activity yet today (so mid-day doesn't break it)
- Speed chart goes full-width on Cycling tab

### Fixed
- Achievements "Running" and "Cycling" categories were empty (category name mismatch)
- Streak emoji ⬜ was invisible on some systems — replaced with visible pill badge

## [0.2.0] – 2024-05
### Added
- Separate Cycling tab with monthly distance, speed trend, distance PRs (10K/20K/50K/100K), yearly chart
- Data range banner ("Data: Jan 2021 → Jun 2026 · 1,247 activities total")
- Recent Activities list on Overview tab (last 8, clickable)
- This Week vs Last Week card
- PRs at a glance on Overview
- Profile picture with athlete info card (top-right dropdown)
- Achievements tab with badge grid
- Stats tab with hero block and sections (General / Distance / Time / Streaks / Elevation)
- Floating Share Card FAB button
- Dynamic sport filter in Activities table
- Leaflet map in activity detail popup (GPS polyline decoded client-side)
- `/api/activity/<id>` endpoint for detailed activity data + polyline

### Changed
- Removed followers/following from profile card
- Yearly distance charts now clip future months (current year ends at current month)
- Fitness Age section removed from Insights tab
- Best Day / Best Time of Day cards removed from Insights

### Fixed
- Favicon SyntaxError (emoji in bytes literal)
- Activity fetch limited to 24 months — removed date filter, bumped per_page to 200

## [0.1.0] – 2024-04
### Added
- Initial release
- Strava OAuth 2.0 connection
- Running stats: PRs (1K–Marathon), pace trend, monthly distance, heatmap
- Training tab: weekly load, sweet spot chart, time of day analysis
- Insights tab: weather vs pace, performance highlights
- Race predictor (Riegel formula)
- VO2max calculator (Jack Daniels VDOT, 5K input)
- Shareable 1080×1080 PNG card (Pillow)
- Demo mode with synthetic data (~700 activities)
- Permanent disk cache per athlete
