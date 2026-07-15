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

def _lookup_customer(search_term):
    """Lookup all VRS and Convo Now numbers for a customer."""
    if not search_term or not search_term.strip():
        return []
    search_term = search_term.strip()
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "number", "operator": "EQ", "value": search_term},
                            {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
                        ]
                    },
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": search_term},
                            {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
                        ]
                    }
                ],
                "properties": ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type"],
                "limit": 100,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            return results
    except Exception as e:
        st.error(f"Error looking up customer: {e}")
    return []

def _lookup_by_email(email, return_all=False):
    """Lookup VRS/Convo Now numbers by email."""
    if not email or not email.strip():
        return [] if return_all else None
    email = email.strip()
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": email},
                            {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                            {"propertyName": "credit_type", "operator": "NEQ", "value": "Guest"}
                        ]
                    }
                ],
                "properties": ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type"],
                "limit": 100,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if return_all:
                return results
            if not results:
                return None
            if len(results) == 1:
                return results[0]
            # Multiple matches - show selector
            st.write(f"**Found {len(results)} matches:**")
            selected_idx = st.selectbox(
                "Select a customer",
                range(len(results)),
                format_func=lambda i: f"{results[i]['properties'].get('first_name', '')} {results[i]['properties'].get('last_name', '')} ({results[i]['properties'].get('number', '—')})"
            )
            return results[selected_idx]
    except Exception as e:
        st.error(f"Error looking up email: {e}")
    return [] if return_all else None

def _lookup_by_name(name, return_all=False):
    """Lookup VRS/Convo Now numbers by first/last name (partial match)."""
    if not name or not name.strip():
        return [] if return_all else None
    name = name.strip()
    try:
        # Search by first_name or last_name containing the input
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "first_name", "operator": "CONTAINS", "value": name},
                            {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                        ]
                    },
                    {
                        "filters": [
                            {"propertyName": "last_name", "operator": "CONTAINS", "value": name},
                            {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                        ]
                    },
                ],
                "properties": ["number", "email", "first_name", "last_name", "number_status", "service_type", "usage_type"],
                "limit": 100,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if return_all:
                return results
            if not results:
                return None
            if len(results) == 1:
                return results[0]
            # Multiple matches - show selector
            st.write(f"**Found {len(results)} matches:**")
            selected_idx = st.selectbox(
                "Select a customer",
                range(len(results)),
                format_func=lambda i: f"{results[i]['properties'].get('first_name', '')} {results[i]['properties'].get('last_name', '')} ({results[i]['properties'].get('number', '—')})"
            )
            return results[selected_idx]
    except Exception as e:
        st.error(f"Error looking up name: {e}")
    return [] if return_all else None

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

def _get_ticket_pipelines():
    """Fetch available ticket pipelines."""
    try:
        resp = requests.get(
            f"{BASE_URL}/crm/v3/pipelines/tickets",
            headers=_headers,
            timeout=10,
        )
        if resp.status_code == 200:
            pipelines = resp.json().get("results", [])
            return {p["label"]: p["id"] for p in pipelines}
    except Exception as e:
        st.error(f"Error fetching pipelines: {e}")
    return {}

def _create_ticket(number_id, subject, description, priority="MEDIUM", pipeline_id=None):
    """Create a new ticket linked to a VRS number."""
    try:
        payload = {
            "properties": {
                "subject": subject,
                "content": description,
                "hs_ticket_priority": priority,
            }
        }
        if pipeline_id:
            payload["properties"]["hs_pipeline"] = pipeline_id
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

# ── Customer Lookup Section ────────────────────────────────────────────────────

st.subheader("🔍 Customer Lookup")

col1, col2 = st.columns([3, 1])
with col1:
    search_input = st.text_input(
        "Search",
        placeholder="Phone number, email, or name...",
        key="customer_search",
        label_visibility="collapsed"
    )
with col2:
    lookup_btn = st.button("Search", use_container_width=True, type="primary")

customer_numbers = []

if lookup_btn or search_input:
    if search_input:
        with st.spinner("Looking up customer..."):
            customer_numbers = _lookup_customer(search_input)

        if customer_numbers:
            st.success(f"✓ Found {len(customer_numbers)} account(s)")
        else:
            st.warning("⚠️ No VRS or Convo Now accounts found.")

# Display all customer accounts in card format
if customer_numbers:
    st.divider()
    st.subheader("📋 Customer Accounts")

    # Get customer info from first account
    first_account = customer_numbers[0].get("properties", {})
    customer_name = f"{first_account.get('first_name', '')} {first_account.get('last_name', '')}".strip() or "—"
    customer_email = first_account.get("email", "—")

    st.write(f"**Customer:** {customer_name} | **Email:** {customer_email}")

    # Display accounts in grid
    cols = st.columns(min(3, len(customer_numbers)))
    for idx, account in enumerate(customer_numbers):
        props = account.get("properties", {})
        number = props.get("number", "—")
        status = props.get("number_status", "—")
        service_type = props.get("service_type", "—")
        usage_type = props.get("usage_type", "—")

        # Color code by service type
        service_color = "#667eea" if service_type == "VRS" else "#764ba2"
        status_color = "#2DB84B" if status == "Live" else "#EF4444"

        with cols[idx % len(cols)]:
            st.markdown(f"""
            <div class="metric-card" style="border-left: 4px solid {service_color};">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                    <div>
                        <strong style="font-size:1.1rem;">{service_type}</strong><br>
                        <small style="color:#6b7280;">{number}</small>
                    </div>
                    <div style="background:{status_color};color:white;padding:4px 12px;border-radius:4px;font-size:0.75rem;font-weight:700;">
                        {status}
                    </div>
                </div>
                <div style="border-top:1px solid #e5e7eb;padding-top:0.75rem;font-size:0.85rem;">
                    <div><small style="color:#6b7280;">Usage Type</small><br>{usage_type}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # Ticket creation form
    st.divider()
    st.subheader("📝 Create Support Ticket")

    # Create a dropdown to select which number to create a ticket for
    number_options = [
        f"{acc.get('properties', {}).get('service_type', '—')} - {acc.get('properties', {}).get('number', '—')}"
        for acc in customer_numbers
    ]
    selected_idx = st.selectbox("Select account for ticket:", range(len(customer_numbers)), format_func=lambda i: number_options[i])
    selected_number = customer_numbers[selected_idx]

    with st.form("ticket_form"):
        subject = st.text_input("Ticket Subject *", placeholder="Brief description of the issue")
        description = st.text_area("Description *", placeholder="Detailed description of the issue", height=120)

        col1, col2 = st.columns(2)
        with col1:
            priority = st.selectbox("Priority", ["MEDIUM", "HIGH", "LOW"], index=0)
        with col2:
            # Fetch and display pipelines
            pipelines = _get_ticket_pipelines()
            if pipelines:
                selected_pipeline = st.selectbox("Pipeline", list(pipelines.keys()))
                pipeline_id = pipelines[selected_pipeline]
            else:
                st.warning("No pipelines available")
                pipeline_id = None

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
                    ticket = _create_ticket(selected_number["id"], subject, description, priority, pipeline_id)
                    if ticket:
                        st.success("✓ Ticket created successfully!")
                        st.markdown(f"""
                        <div class="metric-card">
                            <strong>Ticket ID:</strong> <code>{ticket["id"]}</code><br>
                            <strong>Subject:</strong> {ticket["properties"]["subject"]}<br>
                            <strong>For Account:</strong> {selected_number['properties']['service_type']} - {selected_number['properties']['number']}<br>
                            <strong>Pipeline:</strong> {selected_pipeline if 'pipeline_id' in locals() and pipeline_id else '—'}
                        </div>
                        """, unsafe_allow_html=True)

    # Display existing tickets
    st.divider()
    st.subheader("🎫 Tickets")
    with st.spinner("Loading tickets..."):
        tickets = _get_number_tickets(selected_number["id"])

    if not tickets:
        st.info(f"No tickets found for {selected_number['properties']['service_type']} - {selected_number['properties']['number']}")
    else:
        st.write(f"**{len(tickets)} ticket(s)** for {selected_number['properties']['service_type']} - {selected_number['properties']['number']}")
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
