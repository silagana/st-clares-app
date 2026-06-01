from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import (
    Alumno, Cuota, EstadoAlumnoEnum, EstadoCuotaEnum,
    Imputacion, Inscripcion, MedioPagoEnum, Pago,
)
from db.session import get_session
from services.reconciliation import fifo_distribuir
from utils.audit import log_action
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Efectivo", page_icon="💵", layout="wide")
require_login()
st.title("💵 Pagos en Efectivo")

for k, v in [("ef_msg", None), ("ef_err", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.ef_msg:
    st.success(st.session_state.ef_msg)
    st.session_state.ef_msg = None
if st.session_state.ef_err:
    st.error(st.session_state.ef_err)
    st.session_state.ef_err = None


# ── Load alumnos ───────────────────────────────────────────────────────────────
with get_session() as s:
    alumnos = (
        s.query(Alumno)
        .filter(Alumno.estado == EstadoAlumnoEnum.activo)
        .order_by(Alumno.apellido, Alumno.nombre)
        .all()
    )
    al_opts = {f"{a.apellido}, {a.nombre} — DNI {a.dni}": a.id for a in alumnos}

if not al_opts:
    st.warning("Sin alumnos activos en el sistema.")
    st.stop()


# ── Form ───────────────────────────────────────────────────────────────────────
col_form, col_preview = st.columns([2, 3])

with col_form:
    st.subheader("Registrar pago")

    al_label = st.selectbox("Alumno", list(al_opts.keys()), key="ef_alumno")
    al_id = al_opts[al_label]

    fecha_pago = st.date_input("Fecha de pago", value=date.today(), key="ef_fecha")
    operador = st.text_input("Operador / cajero (opcional)", key="ef_operador")
    observaciones = st.text_area("Observaciones", key="ef_obs", height=80)

    # Load pending cuotas to suggest total
    with get_session() as s:
        cuotas_pend = (
            s.query(Cuota)
            .join(Inscripcion, Cuota.inscripcion_id == Inscripcion.id)
            .filter(
                Inscripcion.alumno_id == al_id,
                Inscripcion.activa == True,
                Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
            )
            .order_by(Cuota.fecha_vencimiento)
            .all()
        )
        total_pendiente = sum(c.saldo_pendiente for c in cuotas_pend)
        cuotas_df = [
            {
                "Tipo": c.tipo.value,
                "Período": c.periodo or "—",
                "Vence": fmt_fecha(c.fecha_vencimiento),
                "Saldo": fmt_moneda(c.saldo_pendiente),
            }
            for c in cuotas_pend
        ]

    monto = st.number_input(
        "Monto recibido ($)",
        min_value=0.01,
        value=float(total_pendiente) if total_pendiente > 0 else 1.00,
        step=100.0,
        format="%.2f",
        key="ef_monto",
    )

with col_preview:
    st.subheader("Vista previa — distribución FIFO")

    if cuotas_df:
        st.caption(f"Cuotas pendientes ({fmt_moneda(total_pendiente)} total):")
        st.dataframe(pd.DataFrame(cuotas_df), use_container_width=True, hide_index=True)
    else:
        st.info("Este alumno no tiene cuotas pendientes.")

    if monto > 0 and cuotas_pend:
        with get_session() as s:
            imps, exc = fifo_distribuir(s, [al_id], Decimal(str(monto)))

        if imps:
            st.caption("Imputación resultante:")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Cuota": i.cuota_descripcion,
                        "A imputar": fmt_moneda(i.monto_a_imputar),
                        "Saldo tras pago": fmt_moneda(i.saldo_restante_cuota),
                    }
                    for i in imps
                ]),
                use_container_width=True, hide_index=True,
            )
            if exc > Decimal("0.00"):
                st.info(f"Excedente sin imputar: {fmt_moneda(exc)}")
        else:
            st.warning("Sin cuotas a imputar.")


# ── Confirm ────────────────────────────────────────────────────────────────────
st.divider()

if not cuotas_pend:
    st.info("Sin cuotas pendientes para este alumno.")
elif st.button("💵 Confirmar y registrar pago", type="primary", key="ef_confirm"):
    with get_session() as s:
        imps, exc = fifo_distribuir(s, [al_id], Decimal(str(monto)))

        if not imps:
            st.session_state.ef_err = "Sin cuotas a imputar para el monto ingresado."
        else:
            pago = Pago(
                fecha=fecha_pago,
                monto=Decimal(str(monto)),
                medio=MedioPagoEnum.efectivo,
                movimiento_bancario_id=None,
                operador_efectivo=operador or None,
                observaciones=observaciones or None,
            )
            s.add(pago)
            s.flush()

            for i in imps:
                s.add(Imputacion(
                    pago_id=pago.id,
                    cuota_id=i.cuota_id,
                    monto_imputado=i.monto_a_imputar,
                ))
                cuota = s.get(Cuota, i.cuota_id)
                nuevo_saldo = cuota.saldo_pendiente - i.monto_a_imputar
                if nuevo_saldo <= Decimal("0.00"):
                    cuota.estado = EstadoCuotaEnum.pagada
                    cuota.saldo_pendiente = Decimal("0.00")
                else:
                    cuota.estado = EstadoCuotaEnum.parcial
                    cuota.saldo_pendiente = nuevo_saldo

            log_action(s, "PAGO_EFECTIVO", "pago", pago.id, {
                "alumno_id": al_id,
                "monto": str(monto),
                "imputaciones": len(imps),
                "excedente": str(exc),
            })

    st.session_state.ef_msg = (
        f"✅ Pago registrado — {fmt_moneda(Decimal(str(monto)))} "
        f"imputado en {len(imps)} cuota(s). "
        + (f"Excedente: {fmt_moneda(exc)}." if exc > Decimal("0.00") else "")
    )
    st.rerun()


# ── Historial reciente ─────────────────────────────────────────────────────────
st.divider()
with st.expander("📋 Historial de pagos en efectivo"):
    with get_session() as s:
        pagos = (
            s.query(Pago)
            .filter(Pago.medio == MedioPagoEnum.efectivo)
            .order_by(Pago.fecha.desc(), Pago.id.desc())
            .limit(50)
            .all()
        )
        hist = [
            {
                "Fecha": fmt_fecha(p.fecha),
                "Monto": fmt_moneda(p.monto),
                "Operador": p.operador_efectivo or "—",
                "Cuotas": len(p.imputaciones),
                "Obs": (p.observaciones or "")[:40],
            }
            for p in pagos
        ]
    if hist:
        st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)
    else:
        st.info("Sin pagos en efectivo registrados.")
