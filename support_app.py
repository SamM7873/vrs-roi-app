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

APP_PASSWORD = get_secret("APP_PASSWORD")
HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")

if not HUBSPOT_TOKEN or not APP_PASSWORD:
    st.error("Missing secrets")
    st.stop()

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("""
    <style>
        .stApp { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .login-container { max-width: 420px; margin: 8vh auto 0; padding: 0 1rem; }
        .login-card {
            background: #fff;
            border-radius: 16px;
            padding: 2.5rem 2rem;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        .logo-area { text-align: center; margin-bottom: 2rem; }
        .logo-icon { font-size: 3rem; margin-bottom: 1rem; }
        .logo-area h1 { font-size: 1.5rem; font-weight: 800; color: #1f2937; margin: 0 0 0.5rem; }
        .logo-area p { color: #6b7280; font-size: 0.9rem; margin: 0; }
    </style>
    <div class="login-container">
        <div class="login-card">
            <div class="logo-area">
                <div class="logo-icon">🎧</div>
                <h1>VRS Support Team</h1>
                <p>Create Support Tickets</p>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="max-width: 420px; margin: 0 auto; padding: 0 1rem;">
    <div style="background: #fff; border-radius: 16px; padding: 0 2rem 2rem; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);">
    """, unsafe_allow_html=True)

    password = st.text_input("Password", type="password")
    if st.button("Sign In", use_container_width=True):
        if password == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Invalid password")

    st.markdown("</div></div>", unsafe_allow_html=True)
    st.stop()

BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

def _lookup_by_email(email):
    if not email or not email.strip():
        return None
    try:
        resp = requests.post(
            f"{BASE_URL}/crm/v3/objects/2-40974683/search",
            headers=_headers,
            json={
                "filterGroups": [[
                    {"propertyName": "email", "operator": "EQ", "value": email.strip()},
                    {"propertyName": "service_type", "operator": "IN", "values": ["VRS", "Convo Now"]},
                ]],
                "properties": ["number", "email", "first_name", "last_name", "service_type"],
                "limit": 1,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            return results[0] if results else None
    except Exception as e:
        st.error(f"Error: {e}")
    return None

def _get_pipelines():
    try:
        resp = requests.get(
            f"{BASE_URL}/crm/v3/pipelines/tickets",
            headers=_headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return {p["label"]: p["id"] for p in resp.json().get("results", [])}
    except:
        pass
    return {}

def _create_ticket(number_id, subject, description, priority, pipeline_id=None):
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
            st.error(f"Error: {resp.status_code}")
            return None

        ticket = resp.json()
        ticket_id = ticket["id"]

        requests.put(
            f"{BASE_URL}/crm/v4/objects/tickets/{ticket_id}/associations/2-40974683/{number_id}",
            headers=_headers,
            json={"id": ticket_id, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationType": "ticket_to_number"}]},
            timeout=10,
        )
        return ticket
    except Exception as e:
        st.error(f"Error: {e}")
    return None

st.markdown("""
<style>
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
.header-section p { margin: 0; opacity: 0.9; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="header-section">
    <h1>🎧 VRS Support Team</h1>
    <p>Create support tickets for VRS/Convo Now customers</p>
</div>
""", unsafe_allow_html=True)

st.subheader("📝 Create Ticket")

with st.form("ticket_form"):
    st.write("**Customer Information**")
    email = st.text_input("Email *", placeholder="customer@example.com")

    st.write("**Ticket Details**")
    subject = st.text_input("Subject *", placeholder="Brief issue description")
    description = st.text_area("Description *", placeholder="Detailed description", height=120)

    col1, col2 = st.columns(2)
    with col1:
        priority = st.selectbox("Priority", ["MEDIUM", "HIGH", "LOW"])
    with col2:
        pipelines = _get_pipelines()
        pipeline_id = None
        if pipelines:
            selected_pipeline = st.selectbox("Pipeline", list(pipelines.keys()))
            pipeline_id = pipelines[selected_pipeline]

    submitted = st.form_submit_button("✓ Create Ticket", use_container_width=True, type="primary")

    if submitted:
        if not email or not subject or not description:
            st.error("Email, Subject, and Description required")
        else:
            with st.spinner("Creating ticket..."):
                account = _lookup_by_email(email)
                if not account:
                    st.error(f"No account found for {email}")
                else:
                    ticket = _create_ticket(account["id"], subject, description, priority, pipeline_id)
                    if ticket:
                        st.success("✓ Ticket created!")
                        props = account.get("properties", {})
                        st.info(f"**ID:** {ticket['id']}\n**Customer:** {props.get('first_name', '')} {props.get('last_name', '')}\n**Account:** {props.get('service_type', '—')} - {props.get('number', '—')}")

st.divider()
if st.button("🚪 Sign Out", use_container_width=True):
    st.session_state.authenticated = False
    st.rerun()
