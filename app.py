import streamlit as st

st.set_page_config(page_title="Кешбек за чек", page_icon="🧾", layout="centered")

navigation = st.navigation(
    [
        st.Page("ui/participant.py", title="Подати чек", icon="🧾", default=True),
        st.Page("ui/admin.py", title="Адміністратор", icon="📊"),
    ]
)
navigation.run()
