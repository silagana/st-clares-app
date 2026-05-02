import pandas as pd
import streamlit as st

from db.models import EstadoMovimientoEnum, MovimientoBancario
from db.session import get_session
from services.bank_import import import_movimientos
from utils.audit import log_action
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Conciliación", page_icon="🏦", layout="wide")
st.title("🏦 Conciliación Bancaria")

for k, v in [("conc_msg", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.conc_msg:
    st.success(st.session_state.conc_msg)
    st.session_state.conc_msg = None


# ── Importar extracto ──────────────────────────────────────────────────────

with st.expander("📥 Importar extracto bancario", expanded=True):
    st.caption(
        "El archivo es el `descargaUltimosMovimientos.xls` del homebanking "
        "(en realidad TSV, encoding latin-1). Podés importarlo varias veces: no duplica."
    )
    uploaded = st.file_uploader(
        "Subir extracto",
        type=["xls", "xlsx", "txt", "tsv", "csv"],
        key="extracto_upload",
    )
    if uploaded:
        content = uploaded.read()
        with get_session() as s:
            imported, skipped = import_movimientos(s, content)
            log_action(s, "IMPORT_EXTRACTO", "movimiento_bancario", None, {
                "archivo": uploaded.name,
                "importados": imported,
                "duplicados": skipped,
            })
        st.session_state.conc_msg = (
            f"✅ Importados {imported} movimientos. "
            f"Duplicados ignorados: {skipped}."
        )
        st.rerun()


# ── Loaders ────────────────────────────────────────────────────────────────

def load_movimientos(estados=None):
    with get_session() as s:
        q = s.query(MovimientoBancario)
        if estados:
            q = q.filter(MovimientoBancario.estado.in_(estados))
        rows = q.order_by(MovimientoBancario.fecha.desc()).all()
        return [
            {
                "id": r.id,
                "Fecha": fmt_fecha(r.fecha),
                "Pagador detectado": r.nombre_pagador_detectado or "—",
                "CUIT": r.cuit_detectado or "—",
                "Monto": fmt_moneda(r.importe),
                "Tipo": (r.tipo_movimiento or "—")[:40],
                "Estado": r.estado.value,
            }
            for r in rows
        ]


def load_stats():
    with get_session() as s:
        total = s.query(MovimientoBancario).count()
        pendientes = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado == EstadoMovimientoEnum.pendiente
        ).count()
        conciliados = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado == EstadoMovimientoEnum.conciliado
        ).count()
        ignorados = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado == EstadoMovimientoEnum.ignorado_no_alumno
        ).count()
        return total, pendientes, conciliados, ignorados


# ── KPIs ───────────────────────────────────────────────────────────────────

total, pendientes, conciliados, ignorados = load_stats()
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total movimientos", total)
k2.metric("Pendientes de conciliar", pendientes)
k3.metric("Conciliados", conciliados)
k4.metric("Ignorados (no alumno)", ignorados)

st.divider()

# ── Tabla de movimientos ───────────────────────────────────────────────────

tab_pend, tab_conc, tab_ignore, tab_todos = st.tabs([
    "⏳ Pendientes",
    "✅ Conciliados",
    "🚫 Ignorados",
    "📋 Todos",
])

with tab_pend:
    movs = load_movimientos([EstadoMovimientoEnum.pendiente, EstadoMovimientoEnum.parcial])
    if movs:
        st.dataframe(pd.DataFrame(movs).drop(columns=["id"]), use_container_width=True, hide_index=True)
        st.caption(f"{len(movs)} movimientos pendientes — conciliación disponible en M4.")
    else:
        st.info("No hay movimientos pendientes. Importá un extracto.")

with tab_conc:
    movs = load_movimientos([EstadoMovimientoEnum.conciliado])
    if movs:
        st.dataframe(pd.DataFrame(movs).drop(columns=["id"]), use_container_width=True, hide_index=True)
    else:
        st.info("Sin movimientos conciliados aún.")

with tab_ignore:
    movs = load_movimientos([EstadoMovimientoEnum.ignorado_no_alumno])
    if movs:
        st.dataframe(pd.DataFrame(movs).drop(columns=["id"]), use_container_width=True, hide_index=True)
        st.caption("Movimientos marcados automáticamente como no-alumno (proveedores, etc.).")
    else:
        st.info("Sin movimientos ignorados.")

with tab_todos:
    movs = load_movimientos()
    if movs:
        st.dataframe(pd.DataFrame(movs).drop(columns=["id"]), use_container_width=True, hide_index=True)
    else:
        st.info("Sin movimientos importados.")
