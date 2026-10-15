# Awisee

A Django studio for signing in, connecting Instagram, Facebook, YouTube, and X, and reading analytics stored in SQLite.

## Run it

```powershell
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000/

Email signup sends a verification link. With `DEBUG=1` and no SMTP server, that link is shown on the verification page. Google sign-in is hidden until `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set in `.env`.

Each network can **Load sample data** before API keys exist. Sample rows are labeled Sample. A live connection replaces them.

## Live connections

Copy `.env.example` to `.env` and add the keys you have. Register these redirect URLs on each developer app:

| Network | Redirect URL |
| --- | --- |
| Google login | `http://127.0.0.1:8000/accounts/google/login/callback/` |
| YouTube | `http://127.0.0.1:8000/connections/youtube/callback/` |
| Facebook | `http://127.0.0.1:8000/connections/facebook/callback/` |
| Instagram | `https://localhost:8000/connections/instagram/callback/` |
| X | `http://127.0.0.1:8000/connections/twitter/callback/` |

Instagram uses [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login): `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, and `INSTAGRAM_REDIRECT_URI` (`https://localhost:8000/connections/instagram/callback/`). The connect scopes are `instagram_business_basic`, `instagram_business_manage_insights`, and `instagram_business_manage_comments`. Insights and comments stay empty until those permissions are granted. Facebook stays on [Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login) for a Page. Connect a professional Instagram account. Facebook uses a Facebook Login for Business configuration in `META_LOGIN_CONFIG_ID`. YouTube needs the Data API and Analytics API enabled. X uses OAuth 2.0 with Read access so the app can store followers and recent posts.

Refresh everything from the command line with:

```powershell
.\.venv\Scripts\python manage.py sync_accounts
.\.venv\Scripts\python manage.py process_youtube_jobs
.\.venv\Scripts\python manage.py process_youtube_jobs --full
.\.venv\Scripts\python manage.py process_instagram_jobs
.\.venv\Scripts\python manage.py process_instagram_jobs --full
.\.venv\Scripts\python manage.py process_facebook_jobs
.\.venv\Scripts\python manage.py process_facebook_jobs --full
```

YouTube sync reads the Data API (channel, uploads, video statistics, comments, caption tracks) and, when the connected channel granted analytics scopes, the Analytics API. Official channel view count and the sum of video views are stored separately. Caption download needs `youtube.force-ssl`. Revenue needs `yt-analytics-monetary.readonly` and a monetized channel; a missing permission leaves revenue empty. The sync does not download video files.

Instagram sync reads the professional profile, paginated media, and comments from `graph.instagram.com`. Account insights (`reach`, `follower_count`, and `total_value` metrics such as `views` and `accounts_engaged`) and per-post insights are requested when the insights scope was granted. `follower_count` on Insights is new followers for that day; the lifetime total stays on the profile. A metric Instagram rejects is skipped. The sync stores permalinks and thumbnail URLs and does not download media files. Reconnect Instagram after this change so the new scopes are granted.

Facebook sync reads the Page already stored on the connection, then that Page's posts and comments from `graph.facebook.com`. Other Pages returned by the login are left alone. A first sync, before a Page id is saved, keeps the Page with the most followers. Follower totals and Page likes stay in separate fields. Daily insights use the current Page metrics (`page_follows`, `page_daily_follows_unique`, `page_media_view`, and the unique media-view metric). If Facebook rejects one of those names, the older impression or fan metric is requested for that name only. The login configuration in `META_LOGIN_CONFIG_ID` needs `pages_show_list`, `pages_read_engagement`, and `pages_read_user_content`. The sync does not download photos or videos.

Data API calls spend quota. Listing and video details are 1 unit per request (videos are batched 50 at a time). `captions.list` is 50 units and `captions.download` is 200, so transcript limits stay low. A default project quota is 10,000 units per day. Analytics reports use a separate quota. The Sync button returns immediately. YouTube, Instagram, and Facebook keep running in the background. Refresh the platform page to see the result. `process_youtube_jobs`, `process_instagram_jobs`, and `process_facebook_jobs` still run the same jobs from the command line.

Set `EMAIL_HOST` when you want real verification mail instead of the local inbox.
