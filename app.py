from pathlib import Path

import streamlit as st
from alembic.config import Config
from alembic import command

from utils.auth import require_login

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "uploads").mkdir(exist_ok=True)
(DATA_DIR / "backups").mkdir(exist_ok=True)

st.set_page_config(
    page_title="St. Clare's App",
    page_icon="🏫",
    layout="wide",
)

require_login()


@st.cache_resource(show_spinner="Inicializando base de datos...")
def run_migrations():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    command.upgrade(cfg, "head")


run_migrations()

# ── Logo en sidebar ────────────────────────────────────────────────────────────
st.logo("assets/logo.png", link="https://stclarescenter.com.ar")

# ── Brand header (home) ────────────────────────────────────────────────────────
col_logo, col_title = st.columns([1, 4], vertical_alignment="center")
with col_logo:
    st.image("assets/logo.png", width=180)
with col_title:
    st.markdown("""
<div style="border-left: 3px solid #3DAA86; padding-left: 1rem;">
    <div style="font-size:1.4rem; font-weight:600; color:#3DAA86;">Sistema de gestión interna</div>
    <div style="font-size:0.85rem; color:#8BC4B2; letter-spacing:1px; text-transform:uppercase; margin-top:2px;">
        Inglés · Buenos Aires
    </div>
</div>
""", unsafe_allow_html=True)

st.divider()
st.caption("Usá el menú lateral para navegar entre secciones.")

col1, col2, col3 = st.columns(3)
col1.metric("Cobrado este mes", "$ -")
col2.metric("Pendiente de conciliar", "$ -")
col3.metric("Alumnos con deuda", "-")
