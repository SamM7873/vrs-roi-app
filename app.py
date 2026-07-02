import streamlit as st

lookup_page = st.Page("pages/0_Lookup.py", title="VRS Lookup", icon="🔍", default=True)
numbers_page = st.Page("pages/1_Numbers_Report.py", title="Numbers Report", icon="📊")
ursa_page = st.Page("pages/2_URSA_Login_Report.py", title="URSA Login Report", icon="👤")
geo_page = st.Page("pages/3_Geographic_Report.py", title="Geographic Report", icon="🗺️")
bulk_page = st.Page("pages/4_Bulk_Search.py", title="Bulk Search", icon="🔎")

pg = st.navigation([lookup_page, numbers_page, ursa_page, geo_page, bulk_page])
pg.run()
