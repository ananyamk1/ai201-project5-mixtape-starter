# Project 5: Mixtape Bug Hunt — Submission

Branch: `bugfix/mixtape`. I fixed all 5 bugs, one commit each, plus a regression test.

---

## AI Usage

I used Claude Code through the whole project. Here's how- 
1. I had it summarize each service file: what the file is for and what each function in the fire primariliy does. That got me oriented faster than reading cold. I still read every file myself after, because the summaries skip edge cases.

2. I asked it to trace adding a song to a playlist from the route down to the notification. It laid out the route → service: create_notification chain. I then read notification_service.py line by line to confirm the chain was real and not made up.

3. I found the suspicious code myself, then asked the AI to explain it. Example: I pointed it at update_listening_streak and asked what today.weekday() != 6 does. It confirmed weekday() returns 6 for Sunday. I verified that by running the function with a Sunday date and watching the streak reset.

**Two instances where AI was wrong/incomplete, & I had to correct it:**

- Bug #3 (duplicate search).AI said the outerjoin would return duplicate rows, so the API should show each multi-tag song more than once. I tried to reproduce it and got one result, not several. I ran the query myself selecting raw columns and saw 3 rows for a 3-tag song — but the ORM was collapsing them back to one. So the AI had the cause right but the symptom wrong. The bug is latent, not visible through the API here.
- Bug #2 (feed shows yesterday). The AI said a 24h window would show people from yesterday. I checked the feed and all three friends showed recent listens. The per-friend dedup was hiding the stale events. I had to query the raw events to prove the 24h filter was letting in listens from 10–18 hours ago.

Takeaway: the AI was really good at explaining code I already found and at tracing call chains. It was not reliable at telling me whether a bug actually shows up so I had to run the code to truly know that.

---

## Milestone 1 — Codebase Map

### How it runs
- Start: `FLASK_APP=app:create_app flask run`. Do not use `python app.py` — that double-imports and breaks SQLAlchemy.
- Seed: `python seed_data.py` gives 5 users, 13 songs, 3 playlists, 10 tags.
- Confirmed it responds at `http://127.0.0.1:5000`. `GET /users/<id>` returns JSON. On macOS use `127.0.0.1`, not `localhost`.

### How the app is organized
Three layers:
- `routes/` — reads the request, calls one service, formats the response.
- `services/` — all the logic. The bugs live here.
- `models.py` — the database models.

The pattern is consistent: every route is thin. It pulls fields off the request, calls one service function, and turns a `ValueError` into a 400 or 404. No logic sits in the routes. So every bug traces route → service, and every fix went in `services/`.

### Files and what they do
| File | Role |
|------|------|
| `app.py` | App factory. Sets up SQLite, registers the 4 blueprints (`/songs`, `/playlists`, `/users`, `/feed`). |
| `models.py` | 7 models + 3 join tables. |
| `routes/songs.py` | Search a song, get one, rate one, log a listen. |
| `routes/playlists.py` | Create a playlist, get it, list its songs, add a song. |
| `routes/users.py` | Get a user, their streak, their notifications, mark one read. |
| `routes/feed.py` | Friends listening now, and the activity feed. |
| `services/streak_service.py` | Logs a listen and updates the listening streak. |
| `services/feed_service.py` | Builds the "friends listening now" and activity feeds. |
| `services/search_service.py` | Searches songs by title or artist. |
| `services/notification_service.py` | Creates and reads notifications. Also owns `rate_song` and `add_to_playlist`. |
| `services/playlist_service.py` | Creates playlists and returns their songs. |
| `seed_data.py` | Fills the DB with test data. |

### The data model
Models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`.

Join tables:
- `friendships` — user to user, stored both directions.
- `song_tags` — song to tag.
- `playlist_entries` — playlist to song, and it carries extra columns: `position` (songs have a set order), `added_by`, `added_at`.

Two things worth noting. A `Rating` is its own table with a unique constraint on (user, song) — one rating per user per song, and re-rating updates the row. And a playlist's song order comes from the `position` column, not insertion order.

### Data flow: adding a song to a playlist sends a notification
`POST /playlists/<id>/songs` with `{song_id, added_by}`:
1. `routes/playlists.py` reads the fields and calls `add_to_playlist`.
2. `add_to_playlist` loads the song, the user, and the playlist.
3. If the song is not already on the playlist, it appends it and commits.
4. If the person adding it is not the person who shared it, it calls `create_notification` for the sharer.
5. The sharer later sees it via `GET /users/<id>/notifications`.

---

## Milestones 2 & 3 - Root Cause Analysis

I reproduced each bug before changing any code. Verified fixes with a clean re-seed: all 15 tests pass, plus direct checks on both sides of every boundary. Seed facts used below: the playlist "Late Night Vibes" has 7 songs. The song "Crown Heights Anthem" has 3 tags and was shared by simone.

### Issue #5: The last song in a playlist never shows up

- **How I reproduced it:** `GET /playlists/<Late Night Vibes>/songs` returned `count = 6`, but the playlist has 7 entries. The last song was missing.
- **How I found it:** traced the route `get_songs` → `playlist_service.get_playlist_songs`. The query orders songs by position correctly, so I read the return line. The slice `songs[:-1]` jumped out.
- **Root cause:** the function returned `[song.to_dict() for song in songs[:-1]]`. The `[:-1]` drops the last item of the list. So the last song by position was always cut, even though the docstring says it returns all songs.
- **Fix and side-effect check:** changed `songs[:-1]` to `songs`. Checked the empty-playlist case still returns `[]` and the order is still by position. `test_playlists.py` passes.

### Issue #4: Notified when a friend adds my song, but not when they rate it

- **How I reproduced it:** simone had 0 notifications. nova rated simone's song (`POST /songs/<id>/rate`, got 201). simone still had 0 notifications.
- **How I found it:** traced the route `rate` → `notification_service.rate_song`. I put it next to `add_to_playlist` in the same file. That one ends with a `create_notification` call. `rate_song` had none.
- **Root cause:** `rate_song` saved the rating and committed, but never created a notification. So the sharer was never told about ratings, even though the playlist path does tell them.
- **Fix and side-effect check:** after the commit, I added a `create_notification` call with type `song_rated`, guarded by `if song.shared_by != user_id` so you are not notified for rating your own song. This copies the self-check that `add_to_playlist` already uses. Checked: a rating by someone else makes exactly one `song_rated` notification; rating your own song makes none; the rating upsert still works. I wrote regression tests for both cases.

### Issue #1: Listening streak keeps resetting (only on Sundays)

- **How I reproduced it:** the bug only happens when today is a Sunday. Today is a Monday, so I called `update_listening_streak(user, now)` directly with a Sunday date. I set the last listen to Saturday and passed Sunday. The streak went from 5 to 1 instead of 6. The same setup on a Monday gave 6.
- **How I found it:** traced the route `listen` → `record_listening_event` → `update_listening_streak`. I read the increment branch. The `and today.weekday() != 6` part had no reason to be there.
- **Root cause:** the increment line was `elif days_since_last == 1 and today.weekday() != 6:`. `datetime.weekday()` returns 6 for Sunday. So on any Sunday, a normal one-day-later listen failed this check and fell through to the `else`, which resets the streak to 1. Consecutive days should always count, no matter the weekday, so the Sunday check was wrong.
- **Fix and side-effect check:** removed `and today.weekday() != 6`. Checked the boundaries: Saturday then Sunday now gives 6; a 2-day gap ending on a Sunday still resets to 1; two listens the same day still don't double-count; a brand new user still starts at 1. `test_streaks.py` passes, including the existing Sunday test.

### Issue #3: The same song shows up twice in search

- **How I reproduced it:** the API did not show duplicates — a search for a 3-tag song returned 1 result. I reproduced the cause at the SQL level instead: running the same join but selecting raw columns returned 3 identical rows for a 3-tag song.
- **How I found it:** traced the route `search` → `search_service.search_songs`. The only thing that could multiply rows was the `outerjoin` to `song_tags`. The raw-column query confirmed it, and confirmed the ORM was the only thing hiding it.
- **Root cause:** the query joined `song_tags`, which produces one row per tag. Tags are not even needed for the filter — they get loaded later in `to_dict()`. So the join adds duplicate rows for multi-tag songs. Right now SQLAlchemy's identity map collapses them back to one, which is why the API looks fine. But that masking depends on the query style, so the duplication is a real latent bug.
- **Fix and side-effect check:** removed the `outerjoin` (and the now-unused `Tag` and `song_tags` imports). The filter only touches `Song.title` and `Song.artist`, so no row can be duplicated now. Checked that search still returns the right songs with their tags. `test_search.py` passes.

### Issue #2: Friends Listening Now shows people from yesterday

- **How I reproduced it:** the feed for nova showed all three friends with recent listens, so the symptom was hidden at first. I queried the raw events and saw the filter was letting in friend listens from 10 and 18 hours ago. The per-friend dedup was hiding them because each friend also had a fresh listen. A friend whose latest listen was 18 hours ago would show as "listening now".
- **How I found it:** traced the route `listening-now` → `feed_service.get_friends_listening_now`. The filter uses `listened_at >= cutoff`, where `cutoff = now - RECENT_THRESHOLD` and `RECENT_THRESHOLD` was 24 hours. That window is a full day.
- **Root cause:** `RECENT_THRESHOLD` was `timedelta(hours=24)`. "Listening now" should be a short window, but a 24-hour window counts anything from the last day as current. The seed comment says listens from the past 30 minutes should show and older ones should not.
- **Fix and side-effect check:** changed `RECENT_THRESHOLD` to `timedelta(minutes=30)`. Re-seeded and checked: the feed now shows only the three listens from 10, 15, and 20 minutes ago, and drops the 10- and 18-hour-old ones. Checked that `get_activity_feed` is not affected — it does not use `RECENT_THRESHOLD`, it just returns the most recent N events.

---

## Regression Test

`tests/test_notifications.py` was for covering Issue #4. `test_rating_notifies_sharer` rates another user's song and checks the sharer gets one `song_rated` notification. Against the buggy code this would fail, because `rate_song` created no notification (0 instead of 1). `test_self_rating_does_not_notify` checks you are not notified for rating your own song.
The starter repo also ships tests that would have caught two more of these: `test_playlist_returns_all_songs` (Issue #5, expects 5 songs, buggy code returned 4) and `test_streak_increments_on_sunday` (Issue #1, expects the streak to increment on Sunday).
