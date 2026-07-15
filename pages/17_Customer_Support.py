import streamlit as st
import requests
from datetime import datetime, timezone
from utils import require_auth, get_secret, COMMON_CSS, report_header

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Customer Support",
    "Create support tickets for VRS/Convo Now customers",
    section="Tools",
)

HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

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

def _fmt_datetime(v):
    dt = _parse_dt(v)
    return dt.strftime("%b %d, %Y at %I:%M %p") if dt else "—"

def _lookup_number_by_email(email):
    """Lookup VRS/Convo Now number by email."""
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
            if results:
                return results[0]
    except Exception as e:
        st.error(f"Error: {e}")
    return None

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
    except Exception:
        pass
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
            st.error(f"Error creating ticket: {resp.status_code}")
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

st.subheader("📝 Create Support Ticket")

with st.form("ticket_form"):
    # Customer info
    st.write("**Customer Information**")
    customer_email = st.text_input("Email *", placeholder="customer@example.com")

    # Ticket details
    st.write("**Ticket Details**")
    subject = st.text_input("Subject *", placeholder="Brief description of the issue")
    description = st.text_area("Description *", placeholder="Detailed description", height=120)

    col1, col2 = st.columns(2)
    with col1:
        priority = st.selectbox("Priority", ["MEDIUM", "HIGH", "LOW"])
    with col2:
        pipelines = _get_ticket_pipelines()
        if pipelines:
            selected_pipeline = st.selectbox("Pipeline", list(pipelines.keys()))
            pipeline_id = pipelines[selected_pipeline]
        else:
            pipeline_id = None

    submitted = st.form_submit_button("✓ Create Ticket", use_container_width=True, type="primary")

    if submitted:
        if not customer_email or not subject or not description:
            st.error("Email, Subject, and Description are required.")
        else:
            with st.spinner("Looking up customer and creating ticket..."):
                account = _lookup_number_by_email(customer_email)
                if not account:
                    st.error(f"No VRS or Convo Now account found for {customer_email}")
                else:
                    ticket = _create_ticket(account["id"], subject, description, priority, pipeline_id)
                    if ticket:
                        st.success("✓ Ticket created successfully!")
                        props = account.get("properties", {})
                        st.info(f"""
                        **Ticket ID:** {ticket["id"]}
                        **Customer:** {props.get('first_name', '')} {props.get('last_name', '')}
                        **Account:** {props.get('service_type', '—')} - {props.get('number', '—')}
                        """)
