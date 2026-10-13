# Awisee

A Django studio for signing in, connecting Instagram, Facebook, YouTube, and LinkedIn, and reading analytics stored in SQLite.

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
| Instagram | `http://127.0.0.1:8000/connections/instagram/callback/` |
| LinkedIn | `http://127.0.0.1:8000/connections/linkedin/callback/` |

Instagram uses the official Graph API, so the account needs to be a professional account linked to a Facebook Page you manage. YouTube needs the Data API and Analytics API enabled. LinkedIn’s standard profile permission returns identity, not follower counts.

Refresh everything from the command line with:

```powershell
.\.venv\Scripts\python manage.py sync_accounts
```

Set `EMAIL_HOST` when you want real verification mail instead of the local inbox.
