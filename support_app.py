import streamlit as st
import requests
from datetime import datetime, timezone
from utils import get_secret

st.set_page_config(
    page_title="VRS Support Team",
    layout="wide",
    page_icon="🎧",
    initial_sidebar_state="collapsed"
)

# ── Authentication ────────────────────────────────────────────────────────────

APP_PASSWORD = get_secret("APP_PASSWORD")
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")

if not HUBSPOT_TOKEN:
    st.error("HUBSPOT_TOKEN is not set. Configure it in secrets.")
    st.stop()

if not APP_PASSWORD:
    st.error("APP_PASSWORD is not set. Configure it in secrets.")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            -webkit-font-smoothing: antialiased;
        }
        .stApp { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .login-container {
            max-width: 420px;
            margin: 8vh auto 0;
            padding: 0 1rem;
        }
        .login-card {
            background: #fff;
            border-radius: 16px;
            padding: 2.5rem 2rem;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        .logo-area {
            text-align: center;
            margin-bottom: 2rem;
        }
        .logo-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        .logo-area h1 {
            font-size: 1.5rem;
            font-weight: 800;
            color: #1f2937;
            margin: 0 0 0.5rem;
            letter-spacing: -0.5px;
        }
        .logo-area p {
            color: #6b7280;
            font-size: 0.9rem;
            margin: 0;
        }
        .stTextInput > div > div > input {
            border-radius: 10px !important;
            border: 1.5px solid #e5e7eb !important;
            padding: 0.75rem 1rem !important;
            font-size: 0.95rem !important;
            background: #f9fafb !important;
        }
        .stTextInput > div > div > input:focus {
            border-color: #667eea !important;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
            background: #fff !important;
        }
        div.stButton > button {
            background-color: #667eea;
            color: #fff;
            border-radius: 10px;
            border: none;
            padding: 0.75rem 2rem;
            font-weight: 700;
            font-size: 0.95rem;
            width: 100%;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }
        div.stButton > button:hover {
            background-color: #5568d3;
        }
    </style>
    <div class="login-container">
        <div class="login-card">
            <div class="logo-area">
                <div class="logo-icon">🎧</div>
                <h1>VRS Support Team</h1>
                <p>Ticket Management & VRS Lookup</p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="max-width: 420px; margin: 0 auto; padding: 0 1rem;">
    <div style="background: #fff; border-radius: 16px; padding: 0 2rem 2rem; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);">
    """, unsafe_allow_html=True)

    entered_password = st.text_input("Password", type="password", placeholder="Enter password")
    if st.button("Sign In", use_container_width=True):
        if entered_password == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Incorrect password. Please try again.")

    st.markdown("</div></div>", unsafe_allow_html=True)
    st.stop()

# ── Main app (after authentication) ───────────────────────────────────────────

BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.main { background: #f9fafb; }
.stApp { background: #f9fafb; }
[data-testid="stAppViewContainer"] { background: #f9fafb; }
.header-section {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 2rem 1.5rem;
    border-radius: 12px;
    margin-bottom: 2rem;
}
.header-section h1 { margin: 0 0 0.5rem; font-size: 1.8rem; }
.header-section p { margin: 0; opacity: 0.9; font-size: 0.95rem; }
.metric-card {
    background: white;
    border-radius: 12px;
    padding: 1.5rem;
    border: 1px solid #e5e7eb;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.ticket-expander { background: white; border-radius: 10px; border-left: 4px solid #667eea; }
</style>
""", unsafe_allow_html=True)

# Header
st.markdown("""
<div class="header-section">
    <h1>🎧 VRS Support Team</h1>
    <p>Lookup VRS numbers and manage customer support tickets</p>
</div>
""", unsafe_allow_html=True)

# ── Helper functions ─────────────────────────────────────────────────────────

def _parse_dt(v):
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _fmt_date(v):
    dt = _parse_dt(v)
    return dt.strftime("%b %d, %Y") if dt else "—"

def _fmt_datetime(v):
    dt = _parse_dt(v)
    return dt.strftime("%b %d, %Y at %I:%M %p") if dt else "—"

def _lookup_number(phone):
    """Lookup a VRS number by phone number."""
    if not phone or not phone.strip():
        return None
    phone = phone.strip()
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [[{"propertyName": "number_", "operator": "EQ", "value": phone}]],
                "properties": ["number_", "contact_name", "email", "number_status", "service_type", "organization"],
                "limit": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]
    except Exception as e:
        st.error(f"Error looking up number: {e}")
    return None

def _lookup_by_email(email):
    """Lookup a VRS number by email."""
    if not email or not email.strip():
        return None
    email = email.strip().lower()
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [[{"propertyName": "email", "operator": "EQ", "value": email}]],
                "properties": ["number_", "contact_name", "email", "number_status", "service_type", "organization"],
                "limit": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]
    except Exception as e:
        st.error(f"Error looking up email: {e}")
    return None

def _lookup_by_name(name):
    """Lookup a VRS number by contact name (partial match)."""
    if not name or not name.strip():
        return None
    name = name.strip()
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [[{"propertyName": "contact_name", "operator": "CONTAINS", "value": name}]],
                "properties": ["number_", "contact_name", "email", "number_status", "service_type", "organization"],
                "limit": 10,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if not results:
                return None
            if len(results) == 1:
                return results[0]
            # Multiple matches - show selector
            st.write(f"**Found {len(results)} matches:**")
            selected_idx = st.selectbox(
                "Select a customer",
                range(len(results)),
                format_func=lambda i: f"{results[i]['properties'].get('contact_name', '—')} ({results[i]['properties'].get('number_', '—')})"
            )
            return results[selected_idx]
    except Exception as e:
        st.error(f"Error looking up name: {e}")
    return None

def _get_number_tickets(number_id):
    """Fetch all tickets associated with a VRS number."""
    try:
        resp = requests.get(
            f"{BASE_URL}/crm/v4/objects/2-40974683/{number_id}/associations/tickets",
            headers=_headers,
            timeout=10,
        )
        if resp.status_code == 200:
            associations = resp.json().get("results", [])
            ticket_ids = [a["id"] for a in associations]
            if not ticket_ids:
                return []
            tickets = []
            for tid in ticket_ids:
                tr = requests.get(
                    f"{BASE_URL}/crm/v3/objects/tickets/{tid}",
                    headers=_headers,
                    params={"properties": ["subject", "hs_pipeline_stage", "hs_ticket_priority", "createdate", "closed_date", "content"]},
                    timeout=10,
                )
                if tr.status_code == 200:
                    tickets.append(tr.json())
            return tickets
    except Exception as e:
        st.error(f"Error fetching tickets: {e}")
    return []

def _create_ticket(number_id, subject, description, priority="MEDIUM"):
    """Create a new ticket linked to a VRS number."""
    try:
        payload = {
            "properties": {
                "subject": subject,
                "content": description,
                "hs_ticket_priority": priority,
            }
        }
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/tickets",
            headers=_headers,
            json=payload,
            timeout=10,
        )
        if resp.status_code != 201:
            st.error(f"Error creating ticket: {resp.status_code} {resp.text}")
            return None
        ticket = resp.json()
        ticket_id = ticket["id"]
        # Associate ticket with the number
        ar = requests.put(
            f"{BASE_URL}/crm/v4/objects/tickets/{ticket_id}/associations/2-40974683/{number_id}",
            headers=_headers,
            json={"id": ticket_id, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationType": "ticket_to_number"}]},
            timeout=10,
        )
        if ar.status_code not in (200, 204):
            st.warning(f"Ticket created but could not link to number: {ar.status_code}")
        return ticket
    except Exception as e:
        st.error(f"Error creating ticket: {e}")
    return None

# ── VRS Lookup Section ────────────────────────────────────────────────────────

st.subheader("🔍 VRS Lookup")

# Search type selector
search_type = st.radio(
    "Search by",
    ["Phone Number", "Email", "Contact Name"],
    horizontal=True,
    label_visibility="collapsed"
)

col1, col2 = st.columns([3, 1])
with col1:
    if search_type == "Phone Number":
        search_input = st.text_input(
            "Phone Number",
            placeholder="e.g., +1-555-123-4567 or (555) 123-4567",
            key="phone_lookup",
            label_visibility="collapsed"
        )
    elif search_type == "Email":
        search_input = st.text_input(
            "Email",
            placeholder="e.g., customer@example.com",
            key="email_lookup",
            label_visibility="collapsed"
        )
    else:  # Contact Name
        search_input = st.text_input(
            "Contact Name",
            placeholder="e.g., John Doe",
            key="name_lookup",
            label_visibility="collapsed"
        )
with col2:
    lookup_btn = st.button("Search", use_container_width=True, type="primary")

number_obj = None
if lookup_btn or search_input:
    if search_input:
        with st.spinner("Looking up..."):
            if search_type == "Phone Number":
                number_obj = _lookup_number(search_input)
            elif search_type == "Email":
                number_obj = _lookup_by_email(search_input)
            else:  # Contact Name
                number_obj = _lookup_by_name(search_input)
        if number_obj:
            st.success("✓ VRS number found")
        else:
            st.warning(f"⚠️ No VRS number found. Check the {search_type.lower()} and try again.")

# Display number details if found
if number_obj:
    props = number_obj.get("properties", {})
    nid = number_obj.get("id")
    contact_name = props.get("contact_name", "—")
    email = props.get("email", "—")
    phone = props.get("number_", "—")
    status = props.get("number_status", "—")
    service_type = props.get("service_type", "—")
    org = props.get("organization", "—")

    st.divider()

    # Customer info
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Contact Name</small><br><strong>' + contact_name + '</strong></div>', unsafe_allow_html=True)
    with col2:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Email</small><br><strong>' + email + '</strong></div>', unsafe_allow_html=True)
    with col3:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Phone</small><br><strong>' + phone + '</strong></div>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Status</small><br><strong>' + status + '</strong></div>', unsafe_allow_html=True)
    with col2:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Service Type</small><br><strong>' + service_type + '</strong></div>', unsafe_allow_html=True)
    with col3:
        st.markdown('<div class="metric-card"><small style="color:#6b7280;">Organization</small><br><strong>' + org + '</strong></div>', unsafe_allow_html=True)

    # Ticket creation form
    st.divider()
    st.subheader("📝 Create Support Ticket")
    with st.form("ticket_form"):
        subject = st.text_input("Ticket Subject *", placeholder="Brief description of the issue")
        description = st.text_area("Description *", placeholder="Detailed description of the issue", height=120)
        priority = st.selectbox("Priority", ["MEDIUM", "HIGH", "LOW"], index=0)

        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("✓ Create Ticket", use_container_width=True, type="primary")
        with col2:
            st.form_submit_button("Cancel", use_container_width=True)

        if submitted:
            if not subject or not description:
                st.error("❌ Subject and Description are required.")
            else:
                with st.spinner("Creating ticket..."):
                    ticket = _create_ticket(nid, subject, description, priority)
                    if ticket:
                        st.success("✓ Ticket created successfully!")
                        st.markdown(f"""
                        <div class="metric-card">
                            <strong>Ticket ID:</strong> <code>{ticket["id"]}</code><br>
                            <strong>Subject:</strong> {ticket["properties"]["subject"]}
                        </div>
                        """, unsafe_allow_html=True)

    # Display existing tickets
    st.divider()
    st.subheader("🎫 Associated Tickets")
    with st.spinner("Loading tickets..."):
        tickets = _get_number_tickets(nid)

    if not tickets:
        st.info("No tickets found for this number.")
    else:
        st.write(f"**{len(tickets)} ticket(s)**")
        for ticket in tickets:
            tid = ticket["id"]
            props = ticket.get("properties", {})
            subject = props.get("subject", "—")
            priority = props.get("hs_ticket_priority", "—")
            stage = props.get("hs_pipeline_stage", "—")
            created = _fmt_datetime(props.get("createdate"))
            closed = _fmt_date(props.get("closed_date"))
            content = props.get("content", "—")

            # Color code priority
            priority_color = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}.get(priority, "#6b7280")

            with st.expander(f"🎫 {subject} · <span style='color:{priority_color};font-weight:700;'>{priority}</span> · {created}", unsafe_allow_html=True):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Ticket ID</small><br><code>{tid}</code></div>', unsafe_allow_html=True)
                with col2:
                    st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Priority</small><br><strong>{priority}</strong></div>', unsafe_allow_html=True)
                with col3:
                    st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Stage</small><br><strong>{stage}</strong></div>', unsafe_allow_html=True)

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Created</small><br>{created}</div>', unsafe_allow_html=True)
                with col2:
                    st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Closed</small><br>{closed}</div>', unsafe_allow_html=True)

                st.markdown(f'<div class="metric-card"><small style="color:#6b7280;">Description</small><br>{content if content and content != "—" else "<em>(No description)</em>"}</div>', unsafe_allow_html=True)

# Logout button
st.divider()
if st.button("🚪 Sign Out", use_container_width=True):
    st.session_state.authenticated = False
    st.rerun()
