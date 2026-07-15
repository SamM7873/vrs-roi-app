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

def _create_ticket(number_id, contact_id, subject, description, priority="MEDIUM"):
    """Create a new ticket linked to a VRS number and contact."""
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
        assoc_payload = {
            "id": ticket_id,
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationType": "ticket_to_number"}]
        }
        ar = requests.put(
            f"{BASE_URL}/crm/v4/objects/tickets/{ticket_id}/associations/2-40974683/{number_id}",
            headers=_headers,
            json=assoc_payload,
            timeout=10,
        )
        if ar.status_code not in (200, 204):
            st.warning(f"Ticket created but could not link to number: {ar.status_code}")
        # Also link to contact if available
        if contact_id:
            cr = requests.put(
                f"{BASE_URL}/crm/v4/objects/tickets/{ticket_id}/associations/contacts/{contact_id}",
                headers=_headers,
                json={"id": ticket_id, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationType": "contact_to_ticket"}]},
                timeout=10,
            )
        return ticket
    except Exception as e:
        st.error(f"Error creating ticket: {e}")
    return None

# ── main UI ───────────────────────────────────────────────────────────────────

st.subheader("VRS Lookup")

col1, col2 = st.columns([3, 1])
with col1:
    phone_input = st.text_input("Enter VRS phone number", placeholder="e.g., +1-555-123-4567", key="phone_lookup")
with col2:
    st.markdown("<div style='padding-top: 1.85rem;'></div>", unsafe_allow_html=True)
    lookup_btn = st.button("Search", use_container_width=True)

number_obj = None
if lookup_btn or phone_input:
    if phone_input:
        with st.spinner("Looking up number..."):
            number_obj = _lookup_number(phone_input)
        if number_obj:
            st.success(f"✓ Found VRS number")
        else:
            st.warning("No VRS number found with that phone number.")

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
    st.subheader("Number Details")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption("Contact Name")
        st.write(contact_name)
    with col2:
        st.caption("Email")
        st.write(email)
    with col3:
        st.caption("Phone")
        st.write(phone)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.caption("Status")
        st.write(status)
    with col2:
        st.caption("Service Type")
        st.write(service_type)
    with col3:
        st.caption("Organization")
        st.write(org)

    # Ticket creation form
    st.divider()
    st.subheader("Create Ticket")
    with st.form("ticket_form"):
        subject = st.text_input("Subject *", placeholder="Brief description of the issue")
        description = st.text_area("Description *", placeholder="Detailed description", height=120)
        priority = st.selectbox("Priority", ["MEDIUM", "HIGH", "LOW"])
        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("Create Ticket", use_container_width=True, type="primary")
        with col2:
            st.form_submit_button("Cancel", use_container_width=True)

        if submitted:
            if not subject or not description:
                st.error("Subject and Description are required.")
            else:
                with st.spinner("Creating ticket..."):
                    ticket = _create_ticket(nid, None, subject, description, priority)
                    if ticket:
                        st.success("✓ Ticket created successfully!")
                        st.json({"id": ticket["id"], "subject": ticket["properties"]["subject"]})

    # Display existing tickets
    st.divider()
    st.subheader("Tickets")
    with st.spinner("Loading tickets..."):
        tickets = _get_number_tickets(nid)

    if not tickets:
        st.info("No tickets found for this number.")
    else:
        st.write(f"**{len(tickets)} ticket(s) found**")
        for ticket in tickets:
            tid = ticket["id"]
            props = ticket.get("properties", {})
            subject = props.get("subject", "—")
            priority = props.get("hs_ticket_priority", "—")
            stage = props.get("hs_pipeline_stage", "—")
            created = _fmt_datetime(props.get("createdate"))
            closed = _fmt_date(props.get("closed_date"))
            content = props.get("content", "—")

            with st.expander(f"🎫 {subject} ({priority}) — {created}"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.caption("Ticket ID")
                    st.code(tid)
                with col2:
                    st.caption("Priority")
                    st.write(priority)
                with col3:
                    st.caption("Stage")
                    st.write(stage)

                col1, col2 = st.columns(2)
                with col1:
                    st.caption("Created")
                    st.write(created)
                with col2:
                    st.caption("Closed")
                    st.write(closed)

                st.caption("Description")
                st.write(content if content and content != "—" else "(No description)")
