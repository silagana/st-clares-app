import os

import streamlit as st

_ADMIN_USER = os.getenv("ADMIN_USER", "admin")
_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def require_login():
    """Redirect to login form if not authenticated. Call after set_page_config."""
    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Iniciar sesión")
    with st.form("login_form"):
        user = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", use_container_width=True)
    if submitted:
        if user == _ADMIN_USER and password == _ADMIN_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()
