# AI-Powered Vehicle Penalty Management and Appeals Platform

A FastAPI backend (with a bundled static portal UI) that lets UK drivers
and businesses upload a Penalty Charge Notice, get it automatically
classified and checked for appealability, draft an AI-assisted appeal
(human-reviewed before anything is sent), and submit it via an automated
bot to a sandboxed mock council site. Includes Stripe test-mode billing
for PCN-handling credit packs.

Built as a university Final Project (FPR). This copy of the repository
was reconstructed on 2026-09-06 from the `SourceCode.txt` plain-text
artefact after the original development machine became unavailable — see
`DEPLOYMENT_GUIDE.md` for how to get it running online, and treat this
GitHub repo (not any single laptop) as the source of truth going forward.

## Local development

```bash
cd backend
cp .env.example .env   # then fill in your own keys
docker compose up -d   # starts Postgres on localhost:5432
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --reload
```

In a second terminal, for the submission-bot sandbox target:

```bash
cd mock-pcn-site
pip install -r requirements.txt
uvicorn app:app --reload --port 9000
```

Then open http://localhost:8000.

## Hosting it online

See [`DEPLOYMENT_GUIDE.md`](./DEPLOYMENT_GUIDE.md).
