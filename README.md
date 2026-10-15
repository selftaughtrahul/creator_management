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

Instagram uses [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login): `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, scope `instagram_business_basic`, and `INSTAGRAM_REDIRECT_URI` (`https://localhost:8000/connections/instagram/callback/`). Facebook stays on [Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login) for a Page. Connect a professional Instagram account. Facebook uses a Facebook Login for Business configuration in `META_LOGIN_CONFIG_ID`. YouTube needs the Data API and Analytics API enabled. X uses OAuth 2.0 with Read access so the app can store followers and recent posts.

Refresh everything from the command line with:

```powershell
.\.venv\Scripts\python manage.py sync_accounts
.\.venv\Scripts\python manage.py process_youtube_jobs
.\.venv\Scripts\python manage.py process_youtube_jobs --full
```

YouTube sync reads the Data API (channel, uploads, video statistics, comments, caption tracks) and, when the connected channel granted analytics scopes, the Analytics API. Official channel view count and the sum of video views are stored separately. Caption download needs `youtube.force-ssl`. Revenue needs `yt-analytics-monetary.readonly` and a monetized channel; a missing permission leaves revenue empty. The sync does not download video files.

Data API calls spend quota. Listing and video details are 1 unit per request (videos are batched 50 at a time). `captions.list` is 50 units and `captions.download` is 200, so transcript limits stay low. A default project quota is 10,000 units per day. Analytics reports use a separate quota. Set `YOUTUBE_SYNC_EAGER=0` and let `process_youtube_jobs` do the work so a large channel does not run inside the browser request.

Set `EMAIL_HOST` when you want real verification mail instead of the local inbox.
