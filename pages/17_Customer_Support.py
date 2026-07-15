import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from utils import require_auth, get_secret, COMMON_CSS, report_header

st.markdown(COMMON_CSS, unsafe_allow_html=True)
require_auth()

report_header(
    "Customer Support",
    "VRS lookup and ticket management for the support team",
    section="Tools",
)

HUBSPOT_TOKEN = get_secret("HUBSPOT_TOKEN")
BASE_URL = "https://api.hubapi.com"
_headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}

# ── helpers ───────────────────────────────────────────────────────────────────

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
            # Fetch full ticket details
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

# ── main UI ───────────────────────────────────────────────────────────────────

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
    lookup_btn = st.button("Search", use_container_width=True)

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
            <div style="border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);border-left:4px solid {service_color};">
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
                        <div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
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
                    st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Ticket ID</small><br><code>{tid}</code></div>', unsafe_allow_html=True)
                with col2:
                    st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Priority</small><br><strong>{priority}</strong></div>', unsafe_allow_html=True)
                with col3:
                    st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Stage</small><br><strong>{stage}</strong></div>', unsafe_allow_html=True)

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Created</small><br>{created}</div>', unsafe_allow_html=True)
                with col2:
                    st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Closed</small><br>{closed}</div>', unsafe_allow_html=True)

                st.markdown(f'<div style="background:white;border-radius:12px;padding:1.5rem;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(0,0,0,0.05);"><small style="color:#6b7280;">Description</small><br>{content if content and content != "—" else "<em>(No description)</em>"}</div>', unsafe_allow_html=True)
