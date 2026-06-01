from collections import defaultdict
from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import (
    Alumno, Cuota, EstadoCuotaEnum, Inscripcion, Sede,
)
from db.session import get_session
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Morosos", page_icon="📨", layout="wide")
require_login()
st.title("📨 Morosos")

HOY = date.today()


# ── Load morosos ───────────────────────────────────────────────────────────────
def load_morosos(sede_id=None):
    with get_session() as s:
        q = (
            s.query(Alumno, Cuota, Sede)
            .join(Inscripcion, Alumno.id == Inscripcion.alumno_id)
            .join(Cuota, Inscripcion.id == Cuota.inscripcion_id)
            .join(Sede, Alumno.sede_id == Sede.id)
            .filter(
                Cuota.fecha_vencimiento < HOY,
                Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
                Inscripcion.activa == True,
            )
        )
        if sede_id:
            q = q.filter(Alumno.sede_id == sede_id)
        rows = q.order_by(Alumno.apellido, Alumno.nombre, Cuota.fecha_vencimiento).all()

        # Group by alumno
        alumnos: dict[int, dict] = {}
        sedes_info: dict[int, dict] = {}

        for alumno, cuota, sede in rows:
            if sede.id not in sedes_info:
                sedes_info[sede.id] = {
                    "nombre": sede.nombre,
                    "encargada_nombre": sede.encargada_nombre or "",
                    "encargada_telefono": sede.encargada_telefono or "",
                }
            if alumno.id not in alumnos:
                alumnos[alumno.id] = {
                    "alumno_id": alumno.id,
                    "nombre": f"{alumno.apellido}, {alumno.nombre}",
                    "sede_id": sede.id,
                    "cuotas": [],
                    "total_adeudado": Decimal("0.00"),
                    "vencimiento_mas_antiguo": cuota.fecha_vencimiento,
                }
            alumnos[alumno.id]["cuotas"].append({
                "tipo": cuota.tipo,
                "periodo": cuota.periodo,
                "fecha_vencimiento": cuota.fecha_vencimiento,
                "saldo_pendiente": cuota.saldo_pendiente,
            })
            alumnos[alumno.id]["total_adeudado"] += cuota.saldo_pendiente
            if cuota.fecha_vencimiento < alumnos[alumno.id]["vencimiento_mas_antiguo"]:
                alumnos[alumno.id]["vencimiento_mas_antiguo"] = cuota.fecha_vencimiento

        # Group alumnos by sede
        por_sede: dict[int, dict] = {}
        for a in alumnos.values():
            sid = a["sede_id"]
            if sid not in por_sede:
                por_sede[sid] = {**sedes_info[sid], "sede_id": sid, "morosos": []}
            por_sede[sid]["morosos"].append(a)

        return list(por_sede.values())


def build_mensaje_sede(sede_data: dict) -> str:
    encargada = sede_data["encargada_nombre"] or "Encargada"
    nombre_sede = sede_data["nombre"]
    lines = [
        f"Hola {encargada}, te paso el resumen de morosos de *{nombre_sede}* al {fmt_fecha(HOY)}:",
        "",
    ]
    for m in sede_data["morosos"]:
        nombre = m["nombre"]
        total = fmt_moneda(m["total_adeudado"])
        cuotas_desc = []
        for c in m["cuotas"]:
            tipo_label = {
                "matricula": "Matrícula",
                "mensual": f"Cuota {c['periodo'] or ''}".strip(),
                "examen": "Examen",
            }.get(c["tipo"].value, c["tipo"].value)
            cuotas_desc.append(f"{tipo_label} ({fmt_fecha(c['fecha_vencimiento'])})")
        lines.append(f"• *{nombre}* — {total}")
        lines.append(f"  {' | '.join(cuotas_desc)}")
    lines += ["", "Por favor coordiná el contacto con cada uno. Gracias 🙏"]
    return "\n".join(lines)


# ── Filtros ────────────────────────────────────────────────────────────────────
with get_session() as s:
    sedes = s.query(Sede).filter(Sede.activa == True).order_by(Sede.nombre).all()
    sede_opts = {"Todas las sedes": None} | {sede.nombre: sede.id for sede in sedes}

col_f1, _ = st.columns([2, 4])
sede_sel = col_f1.selectbox("Sede", list(sede_opts.keys()), key="mor_sede")
sede_id_filtro = sede_opts[sede_sel]

datos_por_sede = load_morosos(sede_id_filtro)

# ── KPIs ───────────────────────────────────────────────────────────────────────
total_alumnos = sum(len(d["morosos"]) for d in datos_por_sede)
total_adeudado = sum(
    m["total_adeudado"] for d in datos_por_sede for m in d["morosos"]
)
total_cuotas = sum(
    len(m["cuotas"]) for d in datos_por_sede for m in d["morosos"]
)

k1, k2, k3 = st.columns(3)
k1.metric("Alumnos morosos", total_alumnos)
k2.metric("Cuotas vencidas", total_cuotas)
k3.metric("Total adeudado", fmt_moneda(total_adeudado))

st.divider()

if not datos_por_sede:
    st.success("Sin morosos. ¡Todo al día! 🎉")
    st.stop()

# ── Tabla resumen ──────────────────────────────────────────────────────────────
resumen_rows = []
for d in datos_por_sede:
    for m in d["morosos"]:
        resumen_rows.append({
            "Sede": d["nombre"],
            "Alumno": m["nombre"],
            "Cuotas vencidas": len(m["cuotas"]),
            "Total adeudado": fmt_moneda(m["total_adeudado"]),
            "Más antigua": fmt_fecha(m["vencimiento_mas_antiguo"]),
        })
resumen_df = pd.DataFrame(resumen_rows)
st.dataframe(resumen_df, use_container_width=True, hide_index=True)

st.divider()

# ── Mensaje por sede ───────────────────────────────────────────────────────────
st.subheader("Mensaje para encargada por sede")

for d in datos_por_sede:
    encargada = d["encargada_nombre"] or "Sin nombre"
    label = f"🏫 {d['nombre']} — {len(d['morosos'])} moroso(s) — Encargada: {encargada}"
    with st.expander(label):
        msg = build_mensaje_sede(d)
        st.text_area(
            "Mensaje (copiar y enviar a la encargada)",
            value=msg,
            height=300,
            key=f"msg_sede_{d['sede_id']}",
        )
        tel = d["encargada_telefono"].replace(" ", "").replace("-", "").replace("+", "")
        if tel:
            if not tel.startswith("549"):
                tel = "549" + tel.lstrip("0")
            st.link_button(
                "📱 Abrir WhatsApp con encargada",
                f"https://wa.me/{tel}",
            )
        else:
            st.caption("Sin teléfono de encargada registrado.")

# ── Exportar ───────────────────────────────────────────────────────────────────
st.divider()
csv = resumen_df.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "⬇️ Exportar morosos CSV",
    data=csv,
    file_name=f"morosos_{HOY.isoformat()}.csv",
    mime="text/csv",
)
