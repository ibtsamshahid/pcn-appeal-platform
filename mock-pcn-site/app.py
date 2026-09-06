"""
Fictional PCN-provider website — a SANDBOX target for the appeal
submission bot, built for demo/report purposes only.

This is NOT a real council or private parking operator website. It is
deliberately a separate, standalone app (its own process, its own port,
no shared code or database with the main backend) so the bot demo
realistically crosses from "our platform" to "an external site" without
ever touching a real third party's system — see the main backend's
README for why automating real council/TfL/operator portals is out of
scope for this project (Terms of Service risk, anti-bot protection, and
it's simply not necessary to prove the concept).

Run with (from this folder, using the SAME venv as the backend — it
already has fastapi/uvicorn/python-multipart installed):
    uvicorn app:app --reload --port 9000
"""
import random
import string

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

app = FastAPI(title="Fictional Council Appeals Portal (SANDBOX — not a real site)")

# In-memory only — resets every restart. This is a demo prop, not a real
# database; the point is to simulate "an external website received this",
# not to build a second product.
_submissions = []


def _generate_reference() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"APL-{suffix}"


PAGE_STYLE = """
<style>
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 600px; margin: 60px auto; color: #1f2933; }
  .banner { background: #fff3da; border: 1px solid #e8c987; padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 24px; }
  label { display: block; font-weight: 600; font-size: 13px; margin-bottom: 4px; }
  input, textarea { width: 100%; padding: 9px 10px; margin-bottom: 16px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
  button { background: #2f6fed; color: white; border: none; padding: 10px 22px; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; }
</style>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return f"""
    <html><head>{PAGE_STYLE}</head><body>
      <div class="banner">This is a fictional sandbox site built for a student
      project demo. It is not a real council or parking-operator website.</div>
      <h1>Fictional Council Appeals Portal</h1>
      <p><a href="/appeal-form">Submit a PCN appeal &rarr;</a></p>
    </body></html>
    """


@app.get("/appeal-form", response_class=HTMLResponse)
def appeal_form():
    return f"""
    <html><head>{PAGE_STYLE}</head><body>
      <div class="banner">Sandbox demo form — not a real council system.</div>
      <h1>Appeal a Penalty Charge Notice</h1>
      <form method="post" action="/appeal-form">
        <label for="pcn_reference">PCN Reference</label>
        <input id="pcn_reference" name="pcn_reference" required />

        <label for="vehicle_registration">Vehicle Registration</label>
        <input id="vehicle_registration" name="vehicle_registration" required />

        <label for="appellant_name">Appellant Name</label>
        <input id="appellant_name" name="appellant_name" required />

        <label for="appeal_text">Appeal Letter</label>
        <textarea id="appeal_text" name="appeal_text" rows="8" required></textarea>

        <button id="submit-btn" type="submit">Submit Appeal</button>
      </form>
    </body></html>
    """


@app.post("/appeal-form", response_class=HTMLResponse)
def appeal_form_submit(
    pcn_reference: str = Form(...),
    vehicle_registration: str = Form(...),
    appellant_name: str = Form(...),
    appeal_text: str = Form(...),
):
    reference = _generate_reference()
    _submissions.append(
        {
            "reference": reference,
            "pcn_reference": pcn_reference,
            "vehicle_registration": vehicle_registration,
            "appellant_name": appellant_name,
            "appeal_text": appeal_text,
        }
    )
    return f"""
    <html><head>{PAGE_STYLE}</head><body>
      <div class="banner">Sandbox demo confirmation — not a real council system.</div>
      <h1>Appeal Received</h1>
      <p id="confirmation-message">Thank you, {appellant_name}. Your appeal for
      vehicle {vehicle_registration} has been received and will be reviewed.</p>
      <p>Your reference number: <strong id="confirmation-reference">{reference}</strong></p>
    </body></html>
    """


@app.get("/submissions")
def list_submissions():
    """Debug helper only — lets you see everything the bot has submitted so
    far without needing a database. Not part of the demo flow itself."""
    return _submissions
