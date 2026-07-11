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


def _sso_configured():
    """True when Streamlit OIDC auth is configured in secrets ([auth] section)."""
    try:
        return bool(st.secrets.get("auth", {}).get("client_id"))
    except Exception:
        return False


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


def require_auth():
    """Login gate. Uses SSO (st.login / OIDC) when [auth] is configured in
    secrets; otherwise falls back to the shared APP_PASSWORD gate."""
    if not HUBSPOT_TOKEN:
        st.error("HUBSPOT_TOKEN is not set.")
        st.stop()

    # ── SSO path (Google / any OIDC provider via Streamlit native auth) ──────
    if _sso_configured():
        if not hasattr(st, "login"):
            st.error("SSO is configured but this Streamlit version has no st.login — "
                     "requires streamlit>=1.42. Check that requirements installed correctly.")
            st.stop()
        if not st.user.is_logged_in:
            st.markdown("""
            <style>
                .stApp { background-color: #F6F8FA; }
                .login-wrap { max-width:400px;margin:10vh auto 0;padding:0 1rem;text-align:center; }
                .logo-mark {
                    display:inline-flex;align-items:center;justify-content:center;
                    width:52px;height:52px;background:#00A651;border-radius:12px;
                    font-size:1.3rem;font-weight:900;color:#fff;margin-bottom:0.75rem;
                }
                div.stButton > button { background-color:#00A651;color:#fff;border-radius:8px;border:none;
                    padding:0.6rem 2.2rem;font-weight:700;font-size:0.95rem;width:100%; }
                div.stButton > button:hover { background-color:#008F46;color:#fff; }
            </style>
            <div class="login-wrap">
              <div class="logo-mark">c</div>
              <h2 style="font-size:1.3rem;font-weight:800;color:#1F2937;margin:0 0 0.25rem;">VRS / Convo Now Lookup</h2>
              <p style="color:#6B7280;font-size:0.85rem;">Sign in with your work account to continue</p>
            </div>
            """, unsafe_allow_html=True)
            _, mid, _ = st.columns([1, 1, 1])
            with mid:
                if st.button("🔐 Sign in with Google", use_container_width=True):
                    st.login()

            with st.expander("🔧 SSO diagnostics"):
                import streamlit as _st_mod
                checks = []
                checks.append(("Streamlit version", _st_mod.__version__,
                               "✅" if hasattr(st, "login") else "❌ needs >= 1.42"))
                try:
                    import authlib
                    checks.append(("Authlib installed", authlib.__version__, "✅"))
                except ImportError:
                    checks.append(("Authlib installed", "MISSING", "❌ add Authlib to requirements + clear cache/reboot"))
                try:
                    auth_cfg = dict(st.secrets.get("auth", {}))
                except Exception:
                    auth_cfg = {}
                for key in ["redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url"]:
                    val = str(auth_cfg.get(key, ""))
                    if key in ("client_secret", "cookie_secret"):
                        shown = f"set ({len(val)} chars)" if val else "MISSING"
                    elif key == "client_id":
                        shown = (val[:12] + "…") if val else "MISSING"
                    else:
                        shown = val or "MISSING"
                    checks.append((f"[auth] {key}", shown, "✅" if val else "❌"))
                ru = str(auth_cfg.get("redirect_uri", ""))
                if ru:
                    if not ru.startswith("https://"):
                        checks.append(("redirect_uri scheme", ru.split(":")[0], "❌ must be https"))
                    if not ru.endswith("/oauth2callback"):
                        checks.append(("redirect_uri path", ru, "❌ must end with /oauth2callback"))
                    try:
                        cur = st.context.url
                        cur_host = cur.split("/")[2] if "://" in cur else "?"
                        ru_host  = ru.split("/")[2]
                        checks.append(("URL host match",
                                       f"app: {cur_host} · redirect: {ru_host}",
                                       "✅" if cur_host == ru_host else "❌ open the app at the redirect_uri host"))
                    except Exception:
                        pass
                allowed = str(get_secret("ALLOWED_EMAILS")) or str(get_secret("ALLOWED_DOMAINS"))
                checks.append(("Allowlist configured", allowed or "MISSING",
                               "✅" if allowed else "❌ set ALLOWED_EMAILS above [auth] or you'll be locked out"))
                st.table(pd.DataFrame(checks, columns=["Check", "Value", "Status"]))
            st.stop()

        email = getattr(st.user, "email", "") or ""
        if not _allowed_email(email):
            st.error(f"{email} is not authorized for this app. "
                     "Access is limited to approved emails — ask an admin to add you to ALLOWED_EMAILS.")
            if st.button("Sign out"):
                st.logout()
            st.stop()

        with st.sidebar:
            st.caption(f"👤 {email}")
            if st.button("Sign out", key="_sso_logout"):
                st.logout()
        return

    # ── Password fallback ─────────────────────────────────────────────────────
    if not APP_PASSWORD:
        return
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
            bar_html = "".join(
                f'<div style="width:14px;height:14px;border-radius:3px;background:{"#2DB84B" if i < filled else "#D1FAE5"};"></div>'
                for i in range(20)
            )
            loader.markdown(f"""{_DASH_CSS}
<div style="background:#fff;border:1.5px solid #E5E7EB;border-radius:14px;
            padding:1.1rem 1.5rem;margin:0.5rem 0;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
  <div style="display:flex;align-items:center;gap:1.25rem;margin-bottom:0.75rem;">
    <div class="dash-wrap">
      <div class="dash uno"></div><div class="dash dos"></div>
      <div class="dash tres"></div><div class="dash cuatro"></div>
    </div>
    <div>
      <div style="font-weight:700;color:#111827;font-size:0.95rem;">{progress_label}</div>
      <div style="color:#6B7280;font-size:0.82rem;">{fetched:,} records fetched</div>
    </div>
  </div>
  <div style="display:flex;gap:3px;">{bar_html}</div>
</div>""", unsafe_allow_html=True)

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
_DASH_CSS = """
<style>
.dash-wrap { display:flex; align-items:center; padding:0 10px; }
.dash {
  margin: 0 15px; width: 35px; height: 15px; border-radius: 8px;
  background: #FF2CBD; box-shadow: 0 0 10px 0 #FECDFF;
}
.dash.uno   { margin-right: -18px; transform-origin: center left;  animation: dspin  3s linear infinite; }
.dash.dos   { transform-origin: center right; animation: dspin2 3s linear infinite; animation-delay: .2s; }
.dash.tres  { transform-origin: center right; animation: dspin3 3s linear infinite; animation-delay: .3s; }
.dash.cuatro{ transform-origin: center right; animation: dspin4 3s linear infinite; animation-delay: .4s; }
@keyframes dspin {
  0% { transform: rotate(0deg); } 25% { transform: rotate(360deg); }
  30% { transform: rotate(370deg); } 35% { transform: rotate(360deg); }
  100% { transform: rotate(360deg); }
}
@keyframes dspin2 {
  0% { transform: rotate(0deg); } 20% { transform: rotate(0deg); }
  30% { transform: rotate(-180deg); } 35% { transform: rotate(-190deg); }
  40% { transform: rotate(-180deg); } 78% { transform: rotate(-180deg); }
  95% { transform: rotate(-360deg); } 98% { transform: rotate(-370deg); }
  100% { transform: rotate(-360deg); }
}
@keyframes dspin3 {
  0% { transform: rotate(0deg); } 27% { transform: rotate(0deg); }
  40% { transform: rotate(180deg); } 45% { transform: rotate(190deg); }
  50% { transform: rotate(180deg); } 62% { transform: rotate(180deg); }
  75% { transform: rotate(360deg); } 80% { transform: rotate(370deg); }
  85% { transform: rotate(360deg); } 100% { transform: rotate(360deg); }
}
@keyframes dspin4 {
  0% { transform: rotate(0deg); } 38% { transform: rotate(0deg); }
  60% { transform: rotate(-360deg); } 65% { transform: rotate(-370deg); }
  75% { transform: rotate(-360deg); } 100% { transform: rotate(-360deg); }
}
</style>
"""

def _dash_card_html(label, sub=""):
    sub_html = f'<div style="color:#6B7280;font-size:0.82rem;">{sub}</div>' if sub else ""
    return f"""{_DASH_CSS}
<div style="display:flex;align-items:center;gap:1.5rem;background:#fff;border:1.5px solid #E5E7EB;
            border-radius:14px;padding:1.1rem 1.5rem;margin:0.5rem 0;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
  <div class="dash-wrap">
    <div class="dash uno"></div><div class="dash dos"></div>
    <div class="dash tres"></div><div class="dash cuatro"></div>
  </div>
  <div>
    <div style="font-weight:700;color:#111827;font-size:0.95rem;">{label}</div>
    {sub_html}
  </div>
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
