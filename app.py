from pathlib import Path

import streamlit as st
from alembic.config import Config
from alembic import command

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "uploads").mkdir(exist_ok=True)
(DATA_DIR / "backups").mkdir(exist_ok=True)


@st.cache_resource(show_spinner="Inicializando base de datos...")
def run_migrations():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    command.upgrade(cfg, "head")


run_migrations()

st.set_page_config(
    page_title="Instituto — Conciliación",
    page_icon="🏫",
    layout="wide",
)

st.title("🏫 Sistema de Conciliación Bancaria")
st.caption("Usá el menú lateral para navegar entre secciones.")

col1, col2, col3 = st.columns(3)
col1.metric("Cobrado este mes", "$ -")
col2.metric("Pendiente de conciliar", "$ -")
col3.metric("Alumnos con deuda", "-")

st.info("M0 operativo — DB inicializada. Continuá con M1: ABMs de Sedes, Cursos y Alumnos.")
