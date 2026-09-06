# Hosting the PCN Appeals Platform online — step-by-step

This covers taking the reconstructed project (from `24150143-Ibtsam Shahid
SourceCode.txt`) from "code on disk" to "live URL", using free-tier hosting.
It assumes no prior deployment experience.

## What you're deploying

A single FastAPI backend (`backend/`) that serves both the API and the
bundled static portal UI (`backend/app/static/index.html`) from the same
origin — there's no separate frontend to host. It talks to a PostgreSQL
database, the Anthropic API (appeal drafting), and Stripe (test-mode
payments), and drives a Playwright browser bot against a small sandbox
site (`mock-pcn-site/`) that stands in for a real council's appeals form.

That means **two services + one database**:

| Piece | What it is | Why it's separate |
|---|---|---|
| `pcn-backend` | The real app | Needs a Docker build (tesseract, poppler, Playwright's Chromium) |
| `pcn-mock-site` | Fake "council" site | Plain Python, no special dependencies |
| `pcn-db` | PostgreSQL | Managed database service |

## Why Render, and the one thing to know about the free tier

Render's free tier is genuinely free (no credit card, no trial clock) and
supports Docker builds, which this project needs. The trade-off, confirmed
from Render's own docs today: **a free PostgreSQL database is deleted 30
days after creation** (14-day grace period, then gone for good — no
backups on the free tier either). The free *web services* themselves don't
expire, they just fall asleep after 15 minutes of no traffic and wake up
(~1 minute) on the next request.

Practically: this is fine for getting a real, shareable URL to demo to
your supervisor now. If you want the portal to still exist in two months,
either recreate the free database before day 30, or move `pcn-db` to
Render's cheapest paid Postgres plan (small monthly cost) closer to your
viva/deadline. I'm not going to quote an exact price here since it can
change — check the current figure on Render's pricing page before you
decide.

## Reset — delete everything and start clean

If a first attempt went wrong (commonly: the `backend/` and
`mock-pcn-site/` folders didn't actually make it into GitHub, because the
web uploader's "choose your files" dialog can only pick individual files,
not folders — so Render then can't find them and fails with something
like *"Root directory 'mock-pcn-site' does not exist"*), wipe both sides
and redo it with GitHub Desktop instead, which doesn't have that problem.

**Delete the GitHub repo:**
1. Open your repo on github.com → **Settings** (top tab).
2. Scroll all the way down to the red **"Danger Zone"**.
3. Click **Delete this repository**, type the confirmation text it asks
   for exactly as shown, confirm.

**Delete the Render services:**
1. On the Render dashboard, open `pcn-backend` → **Settings** → scroll to
   the bottom → **Delete Web Service**. Confirm.
2. Do the same for `pcn-mock-site`.
3. Open `pcn-db` → **Settings** → **Delete Database**. Confirm.

Now follow Step 1 below again, using GitHub Desktop this time.

## Step 1 — Get the code onto GitHub (using GitHub Desktop — no typing commands)

Render deploys from a git repository, so the code needs a home there
first. GitHub Desktop is a free point-and-click app that avoids the
web-uploader's folder problem entirely.

1. Unzip `pcn-appeals-platform.zip` somewhere easy to find (e.g. your
   Desktop) — right-click it → **Extract All**. You should end up with a
   folder called `project` containing `backend/`, `mock-pcn-site/`, and
   the other files.
2. Install GitHub Desktop: https://desktop.github.com — open it and sign
   in with your GitHub account when it asks (create a free account first
   at https://github.com/signup if you don't have one).
3. In GitHub Desktop: **File → Add local repository**.
4. Click **Choose...** and select that unzipped `project` folder.
5. It will say *"This directory does not appear to be a Git
   repository"* — click **create a repository** in that same message.
6. A dialog appears pre-filled with the folder's name — you can leave it
   or rename it (e.g. `pcn-appeals-platform`) — click **Create
   Repository**.
7. You'll now see a list of every file on the left ("Changes"). At the
   bottom-left, type a summary like `Initial commit`, then click **Commit
   to main**.
8. Click **Publish repository** in the top bar. Untick "Keep this code
   private" only if you don't mind it being public — private is fine and
   Render can still read it. Click **Publish Repository**.

That's it — no command line needed. If you'd rather use `git` on the
command line instead, that works too:
```bash
git init
git add .
git commit -m "Initial commit: reconstructed from FPR source-code artefact"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

## Step 2 — Create a Render account

Go to https://render.com and sign up (signing up with your GitHub account
is easiest — it also grants Render permission to read your repos).

## Step 3 — Deploy using the Blueprint (`render.yaml`)

The repo includes a `render.yaml` that describes all three pieces at once.

1. In the Render dashboard: **New +** → **Blueprint**.
2. Pick the GitHub repo you just pushed. If it doesn't show up in the
   list (common for a private repo the first time), click **Configure
   account** / the GitHub icon and grant Render access to that specific
   repo, then come back and pick it.
3. Render reads `render.yaml` and shows you `pcn-backend`, `pcn-mock-site`,
   and `pcn-db` ready to create together. Click **Apply**.
4. It'll ask you to fill in a few values it can't know on its own
   (marked `sync: false` in the file) — for now you can leave
   `ANTHROPIC_API_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   `FRONTEND_BASE_URL`, and `MOCK_SITE_URL` blank and set them right after
   (next step) — Render lets you edit env vars post-deploy.

If you'd rather click through it manually instead of using the Blueprint,
see "Manual setup" at the bottom of this guide.

## Step 4 — Get your API keys

- **Anthropic API key**: https://console.anthropic.com/settings/keys —
  create a key, copy it.
- **Stripe test keys**: https://dashboard.stripe.com/test/apikeys — copy
  the "Secret key" (starts `sk_test_`). Test keys move no real money;
  Stripe's test card `4242 4242 4242 4242` (any future expiry, any 3-digit
  CVC) simulates a successful payment.
- **Stripe webhook secret**: in the Stripe dashboard, go to
  **Developers → Webhooks → Add endpoint**, set the URL to
  `https://<your-pcn-backend-url>.onrender.com/billing/webhook`, select the
  **`checkout.session.completed`** event (the only one `billing.py`
  listens for), then copy the "Signing secret" (starts `whsec_`).

## Step 5 — Fill in the environment variables on Render

Once both web services have deployed at least once, Render shows you each
one's public URL (something like `https://pcn-backend-xxxx.onrender.com`).
On the `pcn-backend` service, go to **Environment** and set:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 4 |
| `STRIPE_SECRET_KEY` | from step 4 |
| `STRIPE_WEBHOOK_SECRET` | from step 4 |
| `FRONTEND_BASE_URL` | `pcn-backend`'s own URL (no trailing slash) |
| `MOCK_SITE_URL` | `pcn-mock-site`'s URL (no trailing slash) |

Saving env var changes triggers a redeploy automatically.

## Step 6 — Check it's actually live

1. Visit `https://<pcn-backend-url>.onrender.com/health` — should return
   `{"status":"ok"}`. (The very first hit after idle time takes ~30–60s
   while the free instance wakes up — that's expected, not a bug.)
2. Visit `https://<pcn-backend-url>.onrender.com/` — the portal UI should
   load.
3. Register an account, upload a test PCN, and try drafting an appeal to
   confirm the Anthropic key works end to end.
4. Visit `https://<pcn-mock-site-url>.onrender.com/` to confirm the
   sandbox site is up before testing the submission-bot flow.

## Manual setup (if you skip the Blueprint)

1. **New + → PostgreSQL** — name it `pcn-db`, free plan, note the internal
   connection string it gives you.
2. **New + → Web Service** → connect the repo → set **Root Directory** to
   `mock-pcn-site`, environment **Python 3**, build command
   `pip install -r requirements.txt`, start command
   `uvicorn app:app --host 0.0.0.0 --port $PORT`.
3. **New + → Web Service** → connect the repo again → choose **Docker**,
   set **Dockerfile Path** to `backend/Dockerfile` and **Docker Build
   Context** to `backend`. Add the environment variables from the table in
   Step 5, plus `DATABASE_URL` (paste the connection string from step 1),
   `JWT_SECRET_KEY` (any long random string), and `BOT_HEADLESS=true`.

## After this session: the recovered code lives in two places

1. This project's GitHub repo (once you've pushed it) — the durable copy.
   Treat it as the source of truth from now on, not any one laptop.
2. Whatever you rebuild on a replacement/repaired Mac — pull from GitHub
   rather than starting over.
