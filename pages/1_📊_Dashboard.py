from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import cast, func, String

from db.models import (
    Alumno, Cuota, EstadoCuotaEnum, Inscripcion,
    MedioPagoEnum, MovimientoBancario, EstadoMovimientoEnum,
    Pago, Sede,
)
from db.session import get_session
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")
require_login()
st.title("📊 Dashboard")

HOY = date.today()

# ── Filtro de fecha ────────────────────────────────────────────────────────────
with st.expander("📅 Filtro de período", expanded=True):
    fc1, fc2, fc3 = st.columns([2, 2, 3])
    fecha_desde = fc1.date_input("Desde", value=HOY.replace(day=1), format="DD/MM/YYYY", key="dash_desde")
    fecha_hasta = fc2.date_input("Hasta", value=HOY, format="DD/MM/YYYY", key="dash_hasta")
    quick = fc3.selectbox(
        "Período rápido",
        ["Personalizado", "Este mes", "Mes anterior", "Este año", "Todo el tiempo"],
        key="dash_quick",
    )

    if quick == "Este mes":
        fecha_desde = HOY.replace(day=1)
        fecha_hasta = HOY
    elif quick == "Mes anterior":
        from dateutil.relativedelta import relativedelta
        primer_mes_ant = (HOY.replace(day=1) - relativedelta(months=1))
        import calendar
        ultimo_dia = calendar.monthrange(primer_mes_ant.year, primer_mes_ant.month)[1]
        fecha_desde = primer_mes_ant
        fecha_hasta = date(primer_mes_ant.year, primer_mes_ant.month, ultimo_dia)
    elif quick == "Este año":
        fecha_desde = HOY.replace(month=1, day=1)
        fecha_hasta = HOY
    elif quick == "Todo el tiempo":
        fecha_desde = date(2000, 1, 1)
        fecha_hasta = date(2099, 12, 31)


# ── Queries ────────────────────────────────────────────────────────────────────
with get_session() as s:

    # ── Banco ──────────────────────────────────────────────────────────────────
    total_banco = s.query(func.sum(MovimientoBancario.importe)).filter(
        MovimientoBancario.fecha.between(fecha_desde, fecha_hasta),
        MovimientoBancario.estado != EstadoMovimientoEnum.ignorado_no_alumno,
    ).scalar() or Decimal("0")

    banco_conciliado = s.query(func.sum(Pago.monto)).filter(
        Pago.fecha.between(fecha_desde, fecha_hasta),
        Pago.medio == MedioPagoEnum.transferencia,
    ).scalar() or Decimal("0")

    banco_pendiente = s.query(func.sum(MovimientoBancario.importe)).filter(
        MovimientoBancario.fecha.between(fecha_desde, fecha_hasta),
        MovimientoBancario.estado.in_([
            EstadoMovimientoEnum.pendiente, EstadoMovimientoEnum.parcial,
        ]),
    ).scalar() or Decimal("0")

    # ── Efectivo ───────────────────────────────────────────────────────────────
    total_efectivo = s.query(func.sum(Pago.monto)).filter(
        Pago.fecha.between(fecha_desde, fecha_hasta),
        Pago.medio == MedioPagoEnum.efectivo,
    ).scalar() or Decimal("0")

    # ── Deuda ──────────────────────────────────────────────────────────────────
    pendiente_total = s.query(func.sum(Cuota.saldo_pendiente)).filter(
        Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial])
    ).scalar() or Decimal("0")

    vencido_sin_pagar = s.query(func.sum(Cuota.saldo_pendiente)).filter(
        Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
        Cuota.fecha_vencimiento < HOY,
    ).scalar() or Decimal("0")

    # ── Alumnos ────────────────────────────────────────────────────────────────
    total_alumnos = s.query(Alumno).count()
    alumnos_morosos = (
        s.query(func.count(func.distinct(Inscripcion.alumno_id)))
        .join(Cuota, Inscripcion.id == Cuota.inscripcion_id)
        .filter(
            Cuota.fecha_vencimiento < HOY,
            Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
            Inscripcion.activa == True,
        )
        .scalar() or 0
    )

    # ── Cuotas por estado ──────────────────────────────────────────────────────
    cuotas_estado = s.query(Cuota.estado, func.count(Cuota.id)).group_by(Cuota.estado).all()

    # ── Recaudación mensual ────────────────────────────────────────────────────
    recaud_rows = (
        s.query(
            func.substr(cast(Pago.fecha, String), 1, 7).label("mes"),
            func.sum(Pago.monto).label("total"),
        )
        .group_by("mes")
        .order_by("mes")
        .all()
    )

    # ── Top morosos ────────────────────────────────────────────────────────────
    top_morosos = (
        s.query(
            Alumno.apellido,
            Alumno.nombre,
            Sede.nombre.label("sede"),
            func.count(Cuota.id).label("n_cuotas"),
            func.sum(Cuota.saldo_pendiente).label("total"),
        )
        .join(Inscripcion, Alumno.id == Inscripcion.alumno_id)
        .join(Cuota, Inscripcion.id == Cuota.inscripcion_id)
        .join(Sede, Alumno.sede_id == Sede.id)
        .filter(
            Cuota.fecha_vencimiento < HOY,
            Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
            Inscripcion.activa == True,
        )
        .group_by(Alumno.id)
        .order_by(func.sum(Cuota.saldo_pendiente).desc())
        .limit(10)
        .all()
    )

total_ingresos = banco_conciliado + total_efectivo


# ── KPIs: Ingresos del período ─────────────────────────────────────────────────
st.subheader(f"Ingresos — {fmt_fecha(fecha_desde)} al {fmt_fecha(fecha_hasta)}")

r1c1, r1c2, r1c3, r1c4, r1c5 = st.columns(5)
r1c1.metric("Ingresó al banco", fmt_moneda(total_banco), help="Movimientos bancarios no ignorados")
r1c2.metric("Conciliado", fmt_moneda(banco_conciliado), help="Pagos via transferencia registrados")
r1c3.metric("Pendiente conciliar", fmt_moneda(banco_pendiente), help="Movimientos bancarios sin conciliar")
r1c4.metric("Efectivo", fmt_moneda(total_efectivo), help="Pagos registrados en efectivo")
r1c5.metric("**Total ingresos**", fmt_moneda(total_ingresos), help="Conciliado + efectivo")

st.divider()

# ── KPIs: Deuda y morosos (sin filtro de fecha — estado actual) ────────────────
st.subheader("Deuda actual (todos los períodos)")

r2c1, r2c2, r2c3, r2c4 = st.columns(4)
r2c1.metric("Pendiente de cobrar", fmt_moneda(pendiente_total))
r2c2.metric("Vencido sin pagar", fmt_moneda(vencido_sin_pagar))
r2c3.metric("Alumnos morosos", alumnos_morosos)
r2c4.metric("Total alumnos", total_alumnos)

st.divider()

# ── Charts ────────────────────────────────────────────────────────────────────
chart_col, estado_col = st.columns([3, 2])

with chart_col:
    st.subheader("Recaudación mensual (histórico)")
    if recaud_rows:
        df_rec = pd.DataFrame(recaud_rows, columns=["Mes", "Total"])
        df_rec["Total"] = df_rec["Total"].apply(float)
        st.bar_chart(df_rec.set_index("Mes").tail(12), use_container_width=True)
    else:
        st.info("Sin datos de recaudación.")

with estado_col:
    st.subheader("Cuotas por estado")
    if cuotas_estado:
        lmap = {"pendiente": "Pendiente", "parcial": "Pago parcial",
                "pagada": "Pagada", "condonada": "Condonada"}
        df_est = pd.DataFrame(
            [(lmap.get(str(e), str(e)), n) for e, n in cuotas_estado],
            columns=["Estado", "Cantidad"],
        ).set_index("Estado")
        st.bar_chart(df_est, use_container_width=True)
    else:
        st.info("Sin cuotas registradas.")

st.divider()

# ── Top morosos ───────────────────────────────────────────────────────────────
st.subheader("Top 10 morosos")
if top_morosos:
    st.dataframe(
        pd.DataFrame([
            {
                "Alumno": f"{r.apellido}, {r.nombre}",
                "Sede": r.sede,
                "Cuotas vencidas": r.n_cuotas,
                "Total adeudado": fmt_moneda(r.total),
            }
            for r in top_morosos
        ]),
        use_container_width=True, hide_index=True,
    )
else:
    st.success("Sin morosos. ¡Todo al día! 🎉")

st.caption(f"Actualizado: {fmt_fecha(HOY)}.")
