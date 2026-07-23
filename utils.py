import streamlit as st
import requests
import pandas as pd
import os
import time
import json
import hmac
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from collections import defaultdict

try:
    import extra_streamlit_components as stx
    _HAS_COOKIES = True
except Exception:
    _HAS_COOKIES = False

_AUTH_COOKIE = "vrs_auth"

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


def _load_app_users():
    """Return {username: password} parsed from the APP_USERS secret.

    Format: comma- or semicolon-separated ``user:password`` pairs, e.g.
    ``APP_USERS = "alice:pw1, bob:pw2"``. Usernames are case-insensitive.
    (Named distinctly from the legacy file-based _load_users above.)
    """
    raw = str(get_secret("APP_USERS", "")).strip()
    users = {}
    for pair in raw.replace(";", ",").split(","):
        if ":" in pair:
            u, _, p = pair.partition(":")
            u, p = u.strip(), p.strip()
            if u and p:
                users[u.lower()] = p
    return users


def _app_admins():
    """Usernames allowed to view the audit log (APP_ADMINS secret,
    comma-separated). Defaults to samuel.mercado if unset."""
    raw = str(get_secret("APP_ADMINS", "")).strip()
    admins = {a.strip().lower() for a in raw.replace(";", ",").split(",") if a.strip()}
    return admins or {"samuel.mercado"}


def is_app_admin(username=None):
    u = (username or st.session_state.get("username", "")).strip().lower()
    return u in _app_admins()


# ── Audit log ────────────────────────────────────────────────────────────────
AUDIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_log")
AUDIT_FILE = os.path.join(AUDIT_DIR, "events.jsonl")


def capture_login_context():
    """Best-effort IP / location / device from the request headers."""
    ip, ua = "Unknown", "Unknown"
    try:
        hdrs = st.context.headers
        ua = hdrs.get("User-Agent", "Unknown")

        def _private(a):
            return a.startswith(("10.", "172.", "192.168.", "127.", "::1", "fc", "fd"))
        for h in ["X-Forwarded-For", "X-Real-Ip", "CF-Connecting-IP", "True-Client-IP", "X-Client-IP"]:
            val = hdrs.get(h, "")
            if val:
                for c in val.split(","):
                    c = c.strip()
                    if c and not _private(c):
                        ip = c
                        break
            if ip != "Unknown":
                break
    except Exception:
        pass
    location = "Unknown"
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}?fields=city,regionName,country,status", timeout=4).json()
        if geo.get("status") == "success":
            location = ", ".join(filter(None, [geo.get("city", ""), geo.get("regionName", ""), geo.get("country", "")])) or "Unknown"
    except Exception:
        pass
    ual = ua.lower()
    device = "Mobile" if ("mobile" in ual or "android" in ual) else ("Tablet" if ("tablet" in ual or "ipad" in ual) else "Desktop")
    return {"ip": ip, "location": location, "device": device, "ua": ua}


def log_audit(username, action, context=None):
    """Append a login/logout audit event. Never raises."""
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        ctx = context or {}
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "username": username or "unknown",
            "action": action,
            "ip": ctx.get("ip", ""),
            "location": ctx.get("location", ""),
            "device": ctx.get("device", ""),
            "ua": ctx.get("ua", ""),
        }
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def read_audit(limit=1000):
    """Return the most recent audit events (newest first)."""
    try:
        with open(AUDIT_FILE) as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]
    except Exception:
        return []


def _auth_timeout_seconds():
    """Idle timeout before a user must log in again (default 6 hours)."""
    try:
        return int(float(get_secret("AUTH_TIMEOUT_HOURS", "6")) * 3600)
    except Exception:
        return 6 * 3600


def _auth_secret():
    return str(get_secret("AUTH_SECRET", "") or APP_PASSWORD or "vrs-roi-fallback-secret")


def _make_token(username):
    payload = {"u": username, "t": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(_auth_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def _read_token(token, max_age):
    """Return the username if the token is valid and not older than max_age."""
    try:
        raw, sig = token.split(".", 1)
        expect = hmac.new(_auth_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if int(time.time()) - int(payload.get("t", 0)) > max_age:
            return None
        return payload.get("u")
    except Exception:
        return None


@st.cache_resource
def _cookie_manager():
    if not _HAS_COOKIES:
        return None
    try:
        return stx.CookieManager(key="vrs_auth_cookies")
    except Exception:
        return None


def require_auth():
    """Login gate. Supports per-user username+password via the APP_USERS
    secret, and falls back to a single shared APP_PASSWORD. Login persists in
    a signed browser cookie with a sliding idle timeout (default 6 h). Call at
    the top of every page."""
    if not HUBSPOT_TOKEN:
        st.error("HUBSPOT_TOKEN is not set.")
        st.stop()

    users = _load_app_users()
    if not users and not APP_PASSWORD:
        st.error("No access control configured — set APP_USERS (user:pass pairs) "
                 "or APP_PASSWORD in secrets.")
        st.stop()
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    timeout = _auth_timeout_seconds()
    cm = _cookie_manager()

    # Restore a session from the signed cookie (survives idle reconnects,
    # but expires after `timeout` seconds of inactivity).
    if not st.session_state.authenticated and cm is not None:
        try:
            tok = cm.get(_AUTH_COOKIE)
        except Exception:
            tok = None
        if tok:
            u = _read_token(tok, timeout)
            if u:
                st.session_state.authenticated = True
                st.session_state.username = u

    if not st.session_state.authenticated:
        st.markdown("""
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
            html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
            .stApp { background-color: #F5F3F0; }
            .login-wrap { max-width:400px;margin:5vh auto 0;padding:0 1rem; }
            .login-logo-area { text-align:center;margin-bottom:1.5rem; }
            .logo-mark {
                display:inline-flex;align-items:center;justify-content:center;
                width:52px;height:52px;background:#C9A876;border-radius:12px;
                font-size:1.3rem;font-weight:900;color:#fff;letter-spacing:-1px;margin-bottom:0.75rem;
            }
            .login-logo-area h2 { font-size:1.3rem;font-weight:800;color:#1F2937;margin:0 0 0.25rem; }
            .login-logo-area p { color:#6B7280;font-size:0.85rem;margin:0; }
            .login-card { background:#fff;border-radius:14px;padding:2rem 1.75rem;border:1px solid #E5E7EB;box-shadow:0 4px 16px rgba(0,0,0,0.06); }
            .stTextInput > div > div > input { border-radius:8px !important;border:1.5px solid #E5E7EB !important;padding:0.6rem 1rem !important;font-size:0.93rem !important;background:#F6F8FA !important; }
            .stTextInput > div > div > input:focus { border-color:#C9A876 !important;box-shadow:0 0 0 3px rgba(201,168,118,0.12) !important;background:#fff !important; }
            div.stButton > button { background-color:#C9A876;color:#fff;border-radius:8px;border:none;padding:0.6rem 2.2rem;font-weight:700;font-size:0.95rem;width:100%;box-shadow:0 1px 4px rgba(201,168,118,0.3); }
            div.stButton > button:hover { background-color:#B59467;color:#fff; }
        </style>
        <div class="login-wrap">
          <div class="login-logo-area">
            <div class="logo-mark">c</div>
            <h2>VRS / Convo Now Lookup</h2>
            <p>Please sign in to continue</p>
          </div>
        <div class="login-card">
        """, unsafe_allow_html=True)
        username = st.text_input("Username", placeholder="Enter username")
        entered = st.text_input("Password", type="password", placeholder="Enter password")
        if st.button("Login"):
            uname = username.strip().lower()
            if users:
                ok = uname in users and entered == users[uname]
            else:
                # no per-user list configured → shared password, any username
                ok = bool(APP_PASSWORD) and entered == APP_PASSWORD
            if ok:
                st.session_state.authenticated = True
                st.session_state.username = username.strip() or "user"
                _ctx = capture_login_context()
                st.session_state.login_info = {**_ctx, "time": datetime.now().strftime("%b %d, %Y at %I:%M %p")}
                log_audit(st.session_state.username, "login", _ctx)
                if cm is not None:
                    try:
                        cm.set(_AUTH_COOKIE, _make_token(st.session_state.username),
                               expires_at=datetime.now() + timedelta(seconds=timeout), key="auth_set")
                        st.session_state._cookie_refreshed = time.time()
                    except Exception:
                        pass
                st.rerun()
            else:
                log_audit(username.strip() or "(blank)", "login_failed",
                          {"ip": capture_login_context().get("ip", "")})
                st.error("Incorrect username or password.")
        st.markdown("</div>", unsafe_allow_html=True)
        st.stop()

    # Sliding refresh: re-stamp the cookie at most every 5 min so continued
    # activity keeps the session alive, but idle time still expires it.
    if cm is not None and time.time() - st.session_state.get("_cookie_refreshed", 0) > 300:
        try:
            cm.set(_AUTH_COOKIE, _make_token(st.session_state.get("username", "user")),
                   expires_at=datetime.now() + timedelta(seconds=timeout), key="auth_refresh")
            st.session_state._cookie_refreshed = time.time()
        except Exception:
            pass

    # ── authenticated: account control in the sidebar ────────────────────────
    with st.sidebar:
        uname = st.session_state.get("username", "user")
        st.markdown(
            f"<div style='margin-top:0.5rem;padding-top:0.75rem;border-top:1px solid rgba(255,255,255,0.15);"
            f"font-size:0.8rem;color:rgba(255,255,255,0.7);'>Logged in as "
            f"<strong style='color:#fff;'>{uname}</strong></div>",
            unsafe_allow_html=True,
        )
        if st.button("Log out", key="_logout_btn", use_container_width=True):
            log_audit(st.session_state.get("username", "user"), "logout")
            if cm is not None:
                try:
                    cm.delete(_AUTH_COOKIE, key="auth_del")
                except Exception:
                    pass
            st.session_state.authenticated = False
            st.session_state.pop("username", None)
            st.session_state.pop("_cookie_refreshed", None)
            st.rerun()


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
<div style="display:flex;align-items:center;gap:1rem;background:#F0FDF4;border:1.5px solid #C9A876;
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
                    f'<div style="width:14px;height:14px;border-radius:3px;background:{"#C9A876" if i < filled else "#E5D5C0"};"></div>'
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
        # HubSpot's search API cannot page past 10,000 records — it returns a
        # 400 on the next request. Stop cleanly at the cap instead of erroring.
        if len(all_results) >= 10000 or int(after) >= 10000:
            break
        time.sleep(0.15)
    return all_results


# ── Dash loading animation (shared across all pages) ────────────────────────
# Steampunk brutalist loader (Uiverse.io by Vivekray898). Pure CSS + divs,
# so it renders through Streamlit's HTML sanitizer (unlike raw <svg>).
_DASH_CSS = """
<style>
.spbl {
  --primary-color:#8b4513; --secondary-color:#b87333; --bg-color:#f5deb3;
  --text-color:#2f1e0e; --border-width:0.25em;
  font-size:8px; width:22em; height:22em; position:relative;
  font-family:"Courier New",Courier,monospace; margin:0.5em auto 0.75em;
}
.spbl .loader-container { width:100%; height:100%; position:relative; transform:rotate(-2deg); }
.spbl .comic-panel {
  width:100%; height:100%; background-color:var(--bg-color);
  border:var(--border-width) solid black; box-shadow:0.5em 0.5em 0 black;
  position:relative; overflow:hidden;
  background-image:radial-gradient(rgba(0,0,0,0.1) 1px,transparent 1px); background-size:10px 10px;
}
.spbl .engine { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
  z-index:2; animation:spbl-rumble 0.5s infinite alternate; }
.spbl .engine-body { width:10em; height:8em; background:var(--primary-color);
  border:var(--border-width) solid black; border-radius:1em; position:relative; }
.spbl .engine-rivet { position:absolute; width:0.5em; height:0.5em; background:#5c2e0e; border-radius:50%; }
.spbl .engine-rivet.tl{top:0.5em;left:0.5em}.spbl .engine-rivet.tr{top:0.5em;right:0.5em}
.spbl .engine-rivet.bl{bottom:0.5em;left:0.5em}.spbl .engine-rivet.br{bottom:0.5em;right:0.5em}
.spbl .loading-plate { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%);
  background:var(--secondary-color); border:var(--border-width) solid black; padding:0.5em 1.5em; z-index:3; }
.spbl .loading-text { font-size:1.5em; font-weight:bold; color:var(--text-color);
  text-transform:uppercase; letter-spacing:0.1em; white-space:nowrap; }
.spbl .gear-container { position:absolute; width:5em; height:5em; z-index:1; }
.spbl .gear { position:absolute; width:100%; height:100%; background:#7a7a7a;
  border:var(--border-width) solid black; border-radius:50%; }
.spbl .gear-tooth { position:absolute; width:1.5em; height:6em; background:#7a7a7a;
  border-top:var(--border-width) solid black; border-bottom:var(--border-width) solid black;
  top:-0.5em; left:1.75em; }
.spbl .gear-tooth:nth-child(2){transform:rotate(60deg)} .spbl .gear-tooth:nth-child(3){transform:rotate(120deg)}
.spbl .gear-container.one { top:2em; left:2em; animation:spbl-cw 4s linear infinite; }
.spbl .gear-container.two { bottom:2em; right:2em; transform:scale(0.8); animation:spbl-ccw 4s linear infinite; }
.spbl .pressure-gauge { position:absolute; top:1.5em; right:1.5em; width:6em; height:3em;
  border:var(--border-width) solid black; border-bottom:none; border-radius:6em 6em 0 0; background:#fff; z-index:3; }
.spbl .gauge-needle { position:absolute; bottom:0; left:50%; width:0.2em; height:2.5em; background:red;
  transform-origin:bottom center; animation:spbl-gauge 2s infinite ease-in-out; }
.spbl .steam-pipe { position:absolute; bottom:0; left:2em; width:2em; height:4em; background:#5c2e0e;
  border:var(--border-width) solid black; }
.spbl .steam-puff { position:absolute; bottom:3.5em; left:1.5em; width:3em; height:3em;
  background:rgba(255,255,255,0.8); border-radius:50%; opacity:0; animation:spbl-puff 3s infinite; }
.spbl .steam-puff:nth-child(2){animation-delay:1.5s}
.spbl .comic-panel::after { content:"HOLD ON!"; position:absolute; bottom:0.5em; left:0.5em;
  background:var(--secondary-color); color:var(--text-color); font-weight:bold; padding:0.3em 0.6em;
  transform:rotate(-5deg); border:var(--border-width) solid black; z-index:4; font-size:0.9em; }
@keyframes spbl-rumble { 0%{transform:translate(-50%,-50%) rotate(0.5deg)} 100%{transform:translate(-50.5%,-49.5%) rotate(-0.5deg)} }
@keyframes spbl-cw { from{transform:rotate(0)} to{transform:rotate(360deg)} }
@keyframes spbl-ccw { from{transform:rotate(0)} to{transform:rotate(-360deg)} }
@keyframes spbl-gauge { 0%,100%{transform:rotate(-45deg)} 50%{transform:rotate(45deg)} }
@keyframes spbl-puff { 0%{transform:scale(0.5) translateY(0);opacity:1} 100%{transform:scale(1.5) translateY(-3em);opacity:0} }
</style>
"""

_LOADER_HTML = """
<div class="spbl"><div class="loader-container"><div class="comic-panel">
  <div class="gear-container one"><div class="gear"></div><div class="gear-tooth"></div><div class="gear-tooth"></div><div class="gear-tooth"></div></div>
  <div class="gear-container two"><div class="gear"></div><div class="gear-tooth"></div><div class="gear-tooth"></div><div class="gear-tooth"></div></div>
  <div class="pressure-gauge"><div class="gauge-needle"></div></div>
  <div class="steam-pipe"><div class="steam-puff"></div><div class="steam-puff"></div></div>
  <div class="engine"><div class="engine-body">
    <div class="engine-rivet tl"></div><div class="engine-rivet tr"></div>
    <div class="engine-rivet bl"></div><div class="engine-rivet br"></div>
    <div class="loading-plate"><span class="loading-text">LOADING...</span></div>
  </div></div>
</div></div></div>
"""


def _dash_card_html(label, sub="", extra=""):
    sub_html = f'<div style="color:#6B7280;font-size:0.82rem;margin-top:0.15rem;">{sub}</div>' if sub else ""
    return f"""{_DASH_CSS}
<div style="background:#fff;border:1.5px solid #E5E7EB;border-radius:14px;
            padding:1rem 1.5rem 1.2rem;margin:0.5rem 0;box-shadow:0 2px 8px rgba(0,0,0,0.06);text-align:center;">
  {_LOADER_HTML}
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
        --primary: #C9A876;
        --primary-dark: #B59467;
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
<div style="background:linear-gradient(135deg,#C9A876 0%,#B59467 100%);
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
