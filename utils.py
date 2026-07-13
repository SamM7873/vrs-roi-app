import streamlit as st
import requests
import pandas as pd
import os
import time
from datetime import datetime
from collections import defaultdict

def get_secret(key, default=""):
    """Read a secret, tolerating a missing secrets.toml (env var fallback)."""
    try:
        return st.secrets.get(key, os.environ.get(key, default))
    except Exception:
        return os.environ.get(key, default)


HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
APP_PASSWORD = get_secret("APP_PASSWORD")

BASE_URL = "https://api.hubapi.com"
headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}


def _allowed_email(email):
    """Access requires the signed-in email to match ALLOWED_EMAILS or
    ALLOWED_DOMAINS in secrets. Deny by default: with no allowlist
    configured, no one is allowed in."""
    email = (email or "").strip().lower()
    if not email:
        return False
    allowed_emails  = [e.strip().lower() for e in str(get_secret("ALLOWED_EMAILS")).split(",") if e.strip()]
    allowed_domains = [d.strip().lower().lstrip("@") for d in str(get_secret("ALLOWED_DOMAINS")).split(",") if d.strip()]
    if email in allowed_emails:
        return True
    domain = email.split("@")[-1]
    return domain in allowed_domains


# ── User accounts (email + personal password, stored hashed on disk) ────────

def _users_file():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "users.json")


def _load_users():
    import json
    try:
        with open(_users_file()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_users(users):
    import json
    with open(_users_file(), "w") as f:
        json.dump(users, f)


def _hash_pw(password, salt):
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000).hex()


def _set_user_password(email, password):
    import secrets as _pysecrets
    users = _load_users()
    salt = _pysecrets.token_hex(16)
    users[email] = {"salt": salt, "hash": _hash_pw(password, salt)}
    _save_users(users)


def _verify_user(email, password):
    rec = _load_users().get(email)
    if not rec:
        return False
    return _hash_pw(password, rec["salt"]) == rec["hash"]


def _is_admin(email):
    admins = [e.strip().lower() for e in str(get_secret("ADMIN_EMAILS")).split(",") if e.strip()]
    return (email or "").strip().lower() in admins


def _smtp_configured():
    return bool(get_secret("SMTP_USER") and get_secret("SMTP_PASSWORD"))


def _send_reset_code(email):
    """Email a 6-digit verification code. Returns the code, or None on failure."""
    import smtplib
    import secrets as _pysecrets
    from email.mime.text import MIMEText

    code = f"{_pysecrets.randbelow(1000000):06d}"
    host = get_secret("SMTP_HOST", "smtp.gmail.com")
    port = int(get_secret("SMTP_PORT", "587"))
    user = get_secret("SMTP_USER")
    pw   = get_secret("SMTP_PASSWORD")
    sender = get_secret("SMTP_FROM", user)

    msg = MIMEText(
        f"Your VRS / Convo Now Lookup verification code is: {code}\n\n"
        "Enter this code in the app to set your password. "
        "If you didn't request this, you can ignore this email."
    )
    msg["Subject"] = "Your verification code"
    msg["From"] = sender
    msg["To"] = email
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(sender, [email], msg.as_string())
        return code, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def require_auth():
    """Login gate: email (allowlist) + personal password, with self-service
    reset verified by the team APP_PASSWORD. Call at the top of every page."""
    if not HUBSPOT_TOKEN:
        st.error("HUBSPOT_TOKEN is not set.")
        st.stop()

    if not APP_PASSWORD:
        st.error("No access control configured — set APP_PASSWORD in secrets.")
        st.stop()
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if not st.session_state.authenticated:
        st.markdown("""
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
            html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
            .stApp { background-color: #F6F8FA; }
            .login-wrap { max-width:400px;margin:5vh auto 0;padding:0 1rem; }
            .login-logo-area { text-align:center;margin-bottom:1.5rem; }
            .logo-mark {
                display:inline-flex;align-items:center;justify-content:center;
                width:52px;height:52px;background:#00A651;border-radius:12px;
                font-size:1.3rem;font-weight:900;color:#fff;letter-spacing:-1px;margin-bottom:0.75rem;
            }
            .login-logo-area h2 { font-size:1.3rem;font-weight:800;color:#1F2937;margin:0 0 0.25rem; }
            .login-logo-area p { color:#6B7280;font-size:0.85rem;margin:0; }
            .login-card { background:#fff;border-radius:14px;padding:2rem 1.75rem;border:1px solid #E5E7EB;box-shadow:0 4px 16px rgba(0,0,0,0.06); }
            .stTextInput > div > div > input { border-radius:8px !important;border:1.5px solid #E5E7EB !important;padding:0.6rem 1rem !important;font-size:0.93rem !important;background:#F6F8FA !important; }
            .stTextInput > div > div > input:focus { border-color:#00A651 !important;box-shadow:0 0 0 3px rgba(0,166,81,0.12) !important;background:#fff !important; }
            div.stButton > button { background-color:#00A651;color:#fff;border-radius:8px;border:none;padding:0.6rem 2.2rem;font-weight:700;font-size:0.95rem;width:100%;box-shadow:0 1px 4px rgba(0,166,81,0.3); }
            div.stButton > button:hover { background-color:#008F46;color:#fff; }
        </style>
        <div class="login-wrap">
          <div class="login-logo-area">
            <div class="logo-mark">c</div>
            <h2>VRS / Convo Now Lookup</h2>
            <p>Please enter your password to continue</p>
          </div>
        <div class="login-card">
        """, unsafe_allow_html=True)
        entered = st.text_input("Password", type="password", placeholder="Enter password")
        if st.button("Login"):
            if entered == APP_PASSWORD:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()


def list_all(object_type_id, properties, progress_label="Loading..."):
    """Fetch all records with an animated loading card."""
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}"
    all_results = []
    after = None
    total_estimate = None
    loader = st.empty()

    def _show(fetched, pct, done=False):
        if done:
            loader.markdown(f"""
<div style="display:flex;align-items:center;gap:1rem;background:#F0FDF4;border:1.5px solid #2DB84B;
            border-radius:14px;padding:1rem 1.5rem;margin:0.5rem 0;">
  <div style="font-size:1.6rem;">✅</div>
  <div>
    <div style="font-weight:700;color:#15803D;font-size:0.95rem;">Done!</div>
    <div style="color:#166534;font-size:0.85rem;">{fetched:,} records loaded</div>
  </div>
</div>""", unsafe_allow_html=True)
        else:
            filled = int(pct / 5)
            bar_html = (
                '<div style="display:flex;gap:3px;justify-content:center;margin-top:0.6rem;">'
                + "".join(
                    f'<div style="width:14px;height:14px;border-radius:3px;background:{"#2DB84B" if i < filled else "#D1FAE5"};"></div>'
                    for i in range(20)
                )
                + "</div>"
            )
            loader.markdown(
                _dash_card_html(progress_label, f"{fetched:,} records fetched", extra=bar_html),
                unsafe_allow_html=True,
            )

    _show(0, 0)
    page_num = 0
    while True:
        params = {"limit": 100, "properties": ",".join(properties)}
        if after:
            params["after"] = after
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(1.0)
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            st.error(f"Error {resp.status_code}: {resp.text}")
            break
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        page_num += 1

        if total_estimate is None:
            total_estimate = max(data.get("total", 0), len(all_results))

        after = data.get("paging", {}).get("next", {}).get("after")
        fetched = len(all_results)

        if after and total_estimate and total_estimate > 0:
            pct = min(int(fetched / total_estimate * 100), 95)
        elif not after:
            pct = 100
        else:
            pct = min(page_num * 5, 90)

        _show(fetched, pct)
        if not after:
            break
        time.sleep(0.15)

    _show(len(all_results), 100, done=True)
    return all_results


def fetch_all(object_type_id, properties, filter_groups=None):
    url = f"{BASE_URL}/crm/v3/objects/{object_type_id}/search"
    all_results = []
    after = None
    while True:
        payload = {"limit": 100, "properties": properties, "filterGroups": filter_groups or []}
        if after:
            payload["after"] = after
        resp = None
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
                if resp.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            except requests.exceptions.RequestException:
                if attempt == 2:
                    st.error("HubSpot did not respond after 3 attempts. Please try again in a minute.")
                    return all_results
                time.sleep(2 * (attempt + 1))
        if resp.status_code != 200:
            st.error(f"Error {resp.status_code}: {resp.text}")
            break
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
        time.sleep(0.15)
    return all_results


# ── Dash loading animation (shared across all pages) ────────────────────────
# Circuit-chip loader (Uiverse.io by Vosoone) — flowing traces into a chip
_DASH_CSS = """
<style>
.chip-loader svg { width: 100%; max-width: 260px; height: auto; display:block; margin:0 auto; }
.chip-loader .trace-bg   { fill:none; stroke:#2a2a2a; stroke-width:4; }
.chip-loader .trace-flow { fill:none; stroke-width:4; stroke-linecap:round;
                           stroke-dasharray:16 24; animation: chipflow .8s linear infinite; }
.chip-loader .trace-flow.purple { stroke:#a855f7; }
.chip-loader .trace-flow.blue   { stroke:#3b82f6; animation-delay:.15s; }
.chip-loader .trace-flow.yellow { stroke:#eab308; animation-delay:.30s; }
.chip-loader .trace-flow.green  { stroke:#22c55e; animation-delay:.45s; }
.chip-loader .trace-flow.red    { stroke:#ef4444; animation-delay:.25s; }
@keyframes chipflow { to { stroke-dashoffset:-40; } }
</style>
"""

_CHIP_SVG = """
<div class="chip-loader">
<svg viewBox="0 0 800 500" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="chipGradient" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2d2d2d"></stop><stop offset="100%" stop-color="#0f0f0f"></stop>
    </linearGradient>
    <linearGradient id="textGradient" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#eeeeee"></stop><stop offset="100%" stop-color="#888888"></stop>
    </linearGradient>
    <linearGradient id="pinGradient" x1="1" y1="0" x2="0" y2="0">
      <stop offset="0%" stop-color="#bbbbbb"></stop><stop offset="50%" stop-color="#888888"></stop>
      <stop offset="100%" stop-color="#555555"></stop>
    </linearGradient>
  </defs>
  <g id="traces">
    <path d="M100 100 H200 V210 H326" class="trace-bg"></path>
    <path d="M100 100 H200 V210 H326" class="trace-flow purple"></path>
    <path d="M80 180 H180 V230 H326" class="trace-bg"></path>
    <path d="M80 180 H180 V230 H326" class="trace-flow blue"></path>
    <path d="M60 260 H150 V250 H326" class="trace-bg"></path>
    <path d="M60 260 H150 V250 H326" class="trace-flow yellow"></path>
    <path d="M100 350 H200 V270 H326" class="trace-bg"></path>
    <path d="M100 350 H200 V270 H326" class="trace-flow green"></path>
    <path d="M700 90 H560 V210 H474" class="trace-bg"></path>
    <path d="M700 90 H560 V210 H474" class="trace-flow blue"></path>
    <path d="M740 160 H580 V230 H474" class="trace-bg"></path>
    <path d="M740 160 H580 V230 H474" class="trace-flow green"></path>
    <path d="M720 250 H590 V250 H474" class="trace-bg"></path>
    <path d="M720 250 H590 V250 H474" class="trace-flow red"></path>
    <path d="M680 340 H570 V270 H474" class="trace-bg"></path>
    <path d="M680 340 H570 V270 H474" class="trace-flow yellow"></path>
  </g>
  <rect x="330" y="190" width="140" height="100" rx="20" ry="20" fill="url(#chipGradient)"
        stroke="#222" stroke-width="3"></rect>
  <g>
    <rect x="322" y="205" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="322" y="225" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="322" y="245" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="322" y="265" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
  </g>
  <g>
    <rect x="470" y="205" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="470" y="225" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="470" y="245" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
    <rect x="470" y="265" width="8" height="10" fill="url(#pinGradient)" rx="2"></rect>
  </g>
  <text x="400" y="240" font-family="Arial, sans-serif" font-size="22" fill="url(#textGradient)"
        text-anchor="middle" alignment-baseline="middle">Loading</text>
  <circle cx="100" cy="100" r="5" fill="#111"></circle><circle cx="80" cy="180" r="5" fill="#111"></circle>
  <circle cx="60" cy="260" r="5" fill="#111"></circle><circle cx="100" cy="350" r="5" fill="#111"></circle>
  <circle cx="700" cy="90" r="5" fill="#111"></circle><circle cx="740" cy="160" r="5" fill="#111"></circle>
  <circle cx="720" cy="250" r="5" fill="#111"></circle><circle cx="680" cy="340" r="5" fill="#111"></circle>
</svg>
</div>
"""


def _dash_card_html(label, sub="", extra=""):
    sub_html = f'<div style="color:#6B7280;font-size:0.82rem;margin-top:0.15rem;">{sub}</div>' if sub else ""
    return f"""{_DASH_CSS}
<div style="background:#fff;border:1.5px solid #E5E7EB;border-radius:14px;
            padding:1rem 1.5rem 1.2rem;margin:0.5rem 0;box-shadow:0 2px 8px rgba(0,0,0,0.06);text-align:center;">
  {_CHIP_SVG}
  <div style="font-weight:700;color:#111827;font-size:0.95rem;margin-top:0.5rem;">{label}</div>
  {sub_html}
  {extra}
</div>"""


from contextlib import contextmanager

@contextmanager
def dash_spinner(label="Loading..."):
    """Drop-in replacement for st.spinner using the dash animation."""
    ph = st.empty()
    ph.markdown(_dash_card_html(label), unsafe_allow_html=True)
    try:
        yield
    finally:
        ph.empty()


REPORT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_cache")


def save_report(report_key, data):
    """Persist a report's results to disk so it survives page reloads and app restarts.
    `data` is any picklable dict (DataFrames, summaries, filter settings).
    A `saved_at` timestamp is added automatically."""
    import pickle
    os.makedirs(REPORT_CACHE_DIR, exist_ok=True)
    data = dict(data)
    data["saved_at"] = time.time()
    with open(os.path.join(REPORT_CACHE_DIR, f"{report_key}.pkl"), "wb") as f:
        pickle.dump(data, f)


def load_report(report_key):
    """Load a previously saved report from disk. Returns None if not saved yet."""
    import pickle
    path = os.path.join(REPORT_CACHE_DIR, f"{report_key}.pkl")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def saved_at_label(data):
    """Human-readable Central Time label for when a saved report was pulled."""
    from datetime import timezone as _tz, timedelta as _td
    ts = (data or {}).get("saved_at")
    if not ts:
        return "unknown"
    dt = datetime.fromtimestamp(ts, tz=_tz.utc)
    offset = -5 if 3 <= dt.month <= 11 else -6
    label = "CDT" if offset == -5 else "CST"
    return dt.astimezone(_tz(_td(hours=offset))).strftime(f"%b %d at %I:%M %p {label}")


def month_key(month_str):
    try:
        return datetime.fromisoformat(month_str.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except Exception:
        return month_str


def month_sort_key(m):
    try:
        return datetime.strptime(m, "%m/%d/%Y")
    except Exception:
        return datetime.min


def norm(s):
    return (s or "").strip().lower()


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


VRS_RATE_PER_MINUTE = 8.33        # legacy constant — use vrs_rate_for_month() for accuracy
CONVO_NOW_RATE_PER_MINUTE = 2.60

# VRS FCC rate schedule: (year, month) >= threshold → rate
_VRS_RATE_SCHEDULE = [
    ((2026, 7), 8.61),   # July 2026 onward
    ((2000, 1), 8.33),   # all earlier months
]

def vrs_rate_for_month(month_str):
    """Return the VRS FCC rate for a given month.
    Accepts YYYY-MM, MM/DD/YYYY, MM/01/YYYY, or a datetime object.
    """
    from datetime import datetime as _dt
    ym = None
    if not month_str:
        return 8.33
    try:
        if isinstance(month_str, _dt):
            ym = (month_str.year, month_str.month)
        elif "-" in str(month_str):
            parts = str(month_str)[:7].split("-")
            ym = (int(parts[0]), int(parts[1]))
        elif "/" in str(month_str):
            parts = str(month_str).split("/")
            if len(parts) == 3:
                # MM/DD/YYYY or MM/01/YYYY
                ym = (int(parts[2]), int(parts[0]))
    except Exception:
        pass
    if not ym:
        return 8.33
    for threshold, rate in _VRS_RATE_SCHEDULE:
        if ym >= threshold:
            return rate
    return 8.33


COMMON_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    :root {
        --primary: #00A651;
        --primary-dark: #008F46;
        --background: #F6F8FA;
        --card: #FFFFFF;
        --text: #1F2937;
        --border: #E5E7EB;
    }
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; color: var(--text); }
    .stApp { background-color: var(--background); }
    section[data-testid="stSidebar"] {
        background-color: #0D3B26;
        border-right: none;
    }
    section[data-testid="stSidebar"] * { color: rgba(255,255,255,0.85) !important; }
    section[data-testid="stSidebar"] a[aria-selected="true"],
    section[data-testid="stSidebar"] [aria-selected="true"] {
        background-color: rgba(0,166,81,0.25) !important;
        color: #fff !important;
        border-radius: 8px;
    }
    div.stButton > button {
        background-color: var(--primary); color: #fff;
        border-radius: 8px; border: none;
        padding: 0.55rem 1.4rem; font-weight: 600;
        font-size: 0.93rem;
        box-shadow: 0 1px 4px rgba(0,166,81,0.25);
        transition: background 0.15s;
    }
    div.stButton > button:hover { background-color: var(--primary-dark); }
    .stTabs [data-baseweb="tab-list"] {
        background-color: var(--background);
        border-radius: 8px; padding: 3px 4px;
        border: 1px solid var(--border);
        gap: 2px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 6px; color: #6B7280;
        font-weight: 500; font-size: 0.88rem;
        padding: 0.35rem 1rem;
    }
    .stTabs [aria-selected="true"] {
        background-color: var(--primary) !important;
        color: #FFFFFF !important; font-weight: 700 !important;
    }
    .stTabs [data-baseweb="tab-border"] { display:none; }
    .stTabs [data-baseweb="tab-panel"] { padding-top:1.25rem; }
    [data-testid="metric-container"] {
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 1rem 1.25rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    [data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
</style>
"""

# Responsive rules shared by every page (including ones with their own theme)
MOBILE_CSS = """
<style>
    /* ── Mobile-friendly layout ─────────────────────────────────────────── */
    @media (max-width: 900px) {
        /* Tile / card grids: collapse fixed multi-column grids to 2-up */
        div[style*="grid-template-columns:repeat(7"],
        div[style*="grid-template-columns:repeat(6"],
        div[style*="grid-template-columns:repeat(5"],
        div[style*="grid-template-columns:repeat(4"],
        div[style*="grid-template-columns: repeat(7"],
        div[style*="grid-template-columns: repeat(6"],
        div[style*="grid-template-columns: repeat(5"],
        div[style*="grid-template-columns: repeat(4"] {
            grid-template-columns: repeat(2, 1fr) !important;
        }
        div[style*="grid-template-columns:repeat(3"],
        div[style*="grid-template-columns: repeat(3"] {
            grid-template-columns: repeat(2, 1fr) !important;
        }
        /* Reduce page padding so content uses the full width */
        .block-container { padding-left: 0.9rem !important; padding-right: 0.9rem !important; }
    }
    @media (max-width: 540px) {
        /* Phones: single-column grids, smaller headline numbers */
        div[style*="grid-template-columns:repeat"],
        div[style*="grid-template-columns: repeat"] {
            grid-template-columns: 1fr 1fr !important;
        }
        div[style*="font-size:1.5rem"], div[style*="font-size:1.45rem"],
        div[style*="font-size:1.4rem"], div[style*="font-size:1.3rem"] {
            font-size: 1.1rem !important;
        }
        .block-container { padding-left: 0.6rem !important; padding-right: 0.6rem !important; }
        /* Report header paddings */
        div[style*="padding:1.75rem 2rem 2rem"] { padding: 1rem !important; }
        div[style*="padding:1.25rem 1.75rem 1rem"] { padding: 1rem 1rem 0.75rem !important; }
    }
    /* Tables and wide charts scroll sideways inside their own container */
    [data-testid="stDataFrame"] { overflow-x: auto; }
</style>
"""

COMMON_CSS = COMMON_CSS + MOBILE_CSS


def report_header(title, subtitle, section="Analytics"):
    st.markdown(f"""
<div style="margin-top:1.5rem;">
<div style="background:linear-gradient(135deg,#00A651 0%,#008F46 100%);
            border-radius:12px 12px 0 0;padding:1.25rem 1.75rem 1rem;">
    <div style="font-size:0.68rem;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;
                color:rgba(255,255,255,0.65);margin-bottom:0.3rem;">{section}</div>
    <div style="font-size:1.45rem;font-weight:800;color:#fff;letter-spacing:-0.3px;">{title}</div>
    <div style="color:rgba(255,255,255,0.75);font-size:0.88rem;margin-top:0.2rem;">{subtitle}</div>
</div>
<div style="background:#FFFFFF;border-radius:0 0 12px 12px;padding:1.75rem 2rem 2rem;
            border:1px solid #E5E7EB;border-top:none;
            box-shadow:0 2px 8px rgba(0,0,0,0.05);margin-bottom:2rem;">
""", unsafe_allow_html=True)


def report_header_close():
    st.markdown("</div></div>", unsafe_allow_html=True)
