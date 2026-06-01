import shutil
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from sqlalchemy import func

from db.models import (
    Alumno, Cuota, EstadoCuotaEnum, EstadoMovimientoEnum,
    Imputacion, Inscripcion, MedioPagoEnum, MovimientoBancario,
    Pago, Sede,
)
from db.session import DB_PATH, get_session
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Reportes", page_icon="📑", layout="wide")
require_login()
st.title("📑 Reportes y Backup")

HOY = date.today()

tab_recaud, tab_ec, tab_mor, tab_backup = st.tabs([
    "📊 Recaudación Excel",
    "🧾 Estado de cuenta PDF",
    "📋 Morosos Excel",
    "💾 Backup",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB: RECAUDACIÓN EXCEL
# ══════════════════════════════════════════════════════════════════════════════
with tab_recaud:
    st.subheader("Reporte de recaudación")

    with get_session() as s:
        sedes_r = s.query(Sede).filter(Sede.activa == True).order_by(Sede.nombre).all()
        sede_opts_r = {"Todas": None} | {s2.nombre: s2.id for s2 in sedes_r}

    rc1, rc2, rc3 = st.columns(3)
    r_desde = rc1.date_input("Desde", value=HOY.replace(day=1), format="DD/MM/YYYY", key="rec_desde")
    r_hasta = rc2.date_input("Hasta", value=HOY, format="DD/MM/YYYY", key="rec_hasta")
    r_sede_label = rc3.selectbox("Sede", list(sede_opts_r.keys()), key="rec_sede")
    r_sede_id = sede_opts_r[r_sede_label]

    if st.button("📊 Generar Excel", key="btn_rec_excel", type="primary"):
        with get_session() as s:
            # ── Pagos con detalle de alumno ──────────────────────────────────
            pagos_q = (
                s.query(Pago)
                .filter(Pago.fecha.between(r_desde, r_hasta))
                .order_by(Pago.fecha, Pago.id)
                .all()
            )

            detalle_rows = []
            for pago in pagos_q:
                # Resolve alumno from first imputacion
                alumno_nombre = "—"
                sede_nombre = "—"
                if pago.imputaciones:
                    first_imp = pago.imputaciones[0]
                    ins = first_imp.cuota.inscripcion if first_imp.cuota else None
                    if ins and ins.alumno:
                        al = ins.alumno
                        alumno_nombre = f"{al.apellido}, {al.nombre}"
                        sede_nombre = al.sede.nombre if al.sede else "—"

                if r_sede_id and sede_nombre != (r_sede_label if r_sede_label != "Todas" else None):
                    pass  # will filter below

                detalle_rows.append({
                    "Fecha": fmt_fecha(pago.fecha),
                    "Alumno": alumno_nombre,
                    "Sede": sede_nombre,
                    "Medio": pago.medio.value,
                    "Monto": float(pago.monto),
                    "Cuotas imputadas": len(pago.imputaciones),
                    "Operador": pago.operador_efectivo or "—",
                })

            if r_sede_id:
                sede_nombre_fil = r_sede_label
                detalle_rows = [r for r in detalle_rows if r["Sede"] == sede_nombre_fil]

            # ── Movimientos banco (no conciliados) ───────────────────────────
            movs_pend = (
                s.query(MovimientoBancario)
                .filter(
                    MovimientoBancario.fecha.between(r_desde, r_hasta),
                    MovimientoBancario.estado.in_([
                        EstadoMovimientoEnum.pendiente, EstadoMovimientoEnum.parcial,
                    ]),
                )
                .order_by(MovimientoBancario.fecha)
                .all()
            )
            banco_pend_rows = [
                {
                    "Fecha": fmt_fecha(m.fecha),
                    "Pagador": m.nombre_pagador_detectado or "—",
                    "CUIT": m.cuit_detectado or "—",
                    "Monto": float(m.importe),
                    "Estado": m.estado.value,
                    "Concepto": (m.concepto_raw or "")[:80],
                }
                for m in movs_pend
            ]

        df_det = pd.DataFrame(detalle_rows) if detalle_rows else pd.DataFrame(
            columns=["Fecha", "Alumno", "Sede", "Medio", "Monto", "Cuotas imputadas", "Operador"]
        )
        df_pend = pd.DataFrame(banco_pend_rows) if banco_pend_rows else pd.DataFrame(
            columns=["Fecha", "Pagador", "CUIT", "Monto", "Estado", "Concepto"]
        )

        total_transfer = df_det[df_det["Medio"] == "transferencia"]["Monto"].sum() if not df_det.empty else 0
        total_efectivo = df_det[df_det["Medio"] == "efectivo"]["Monto"].sum() if not df_det.empty else 0
        total_pend_banco = df_pend["Monto"].sum() if not df_pend.empty else 0

        resumen_data = {
            "Concepto": [
                "Período",
                "Sede",
                "Pagos via transferencia",
                "Pagos en efectivo",
                "Total ingresos cobrados",
                "Pendiente de conciliar (banco)",
            ],
            "Valor": [
                f"{fmt_fecha(r_desde)} — {fmt_fecha(r_hasta)}",
                r_sede_label,
                fmt_moneda(total_transfer),
                fmt_moneda(total_efectivo),
                fmt_moneda(total_transfer + total_efectivo),
                fmt_moneda(total_pend_banco),
            ],
        }

        buf_excel = BytesIO()
        with pd.ExcelWriter(buf_excel, engine="openpyxl") as writer:
            pd.DataFrame(resumen_data).to_excel(writer, sheet_name="Resumen", index=False)
            df_det.to_excel(writer, sheet_name="Detalle pagos", index=False)
            df_pend.to_excel(writer, sheet_name="Pendiente banco", index=False)
        buf_excel.seek(0)

        fname = f"recaudacion_{r_desde.isoformat()}_{r_hasta.isoformat()}.xlsx"
        st.download_button(
            "⬇️ Descargar Excel",
            data=buf_excel,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption(
            f"**Transferencias:** {fmt_moneda(total_transfer)}  ·  "
            f"**Efectivo:** {fmt_moneda(total_efectivo)}  ·  "
            f"**Total:** {fmt_moneda(total_transfer + total_efectivo)}  ·  "
            f"**Pendiente banco:** {fmt_moneda(total_pend_banco)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB: ESTADO DE CUENTA PDF
# ══════════════════════════════════════════════════════════════════════════════
with tab_ec:
    st.subheader("Estado de cuenta individual")

    with get_session() as s:
        alumnos_ec = (
            s.query(Alumno)
            .order_by(Alumno.apellido, Alumno.nombre)
            .all()
        )
        al_opts_ec = {f"{a.apellido}, {a.nombre} — DNI {a.dni}": a.id for a in alumnos_ec}

    if not al_opts_ec:
        st.info("Sin alumnos en el sistema.")
    else:
        ec_label = st.selectbox("Alumno", list(al_opts_ec.keys()), key="ec_alumno")
        ec_al_id = al_opts_ec[ec_label]

        ec_mostrar = st.radio(
            "Mostrar cuotas",
            ["Todas", "Solo pendientes / parciales"],
            horizontal=True,
            key="ec_filtro",
        )

        if st.button("🧾 Generar PDF", key="btn_ec_pdf", type="primary"):
            with get_session() as s:
                al = s.get(Alumno, ec_al_id)
                cuotas_pdf = []
                for ins in al.inscripciones:
                    for c in sorted(ins.cuotas, key=lambda x: x.fecha_vencimiento):
                        if ec_mostrar == "Solo pendientes / parciales" and c.estado not in (
                            EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial
                        ):
                            continue
                        cuotas_pdf.append({
                            "curso": ins.curso.nombre,
                            "tipo": c.tipo.value,
                            "periodo": c.periodo or "—",
                            "vencimiento": fmt_fecha(c.fecha_vencimiento),
                            "monto": fmt_moneda(c.monto_actualizado),
                            "saldo": fmt_moneda(c.saldo_pendiente),
                            "estado": c.estado.value,
                        })
                total_deuda = sum(
                    c.saldo_pendiente
                    for ins in al.inscripciones
                    for c in ins.cuotas
                    if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial)
                )
                al_data = {
                    "nombre": f"{al.apellido}, {al.nombre}",
                    "dni": al.dni,
                    "sede": al.sede.nombre if al.sede else "—",
                    "telefono": al.telefono or "—",
                    "email": al.email or "—",
                    "estado": al.estado.value,
                    "total_deuda": total_deuda,
                }

            # Build PDF
            buf_pdf = BytesIO()
            doc = SimpleDocTemplate(
                buf_pdf,
                pagesize=A4,
                leftMargin=2 * cm, rightMargin=2 * cm,
                topMargin=2 * cm, bottomMargin=2 * cm,
            )
            styles = getSampleStyleSheet()
            story = []

            # Header
            story.append(Paragraph("<b>Estado de Cuenta</b>", styles["Title"]))
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph(f"Fecha de emisión: {fmt_fecha(HOY)}", styles["Normal"]))
            story.append(Spacer(1, 0.5 * cm))

            # Alumno info table
            info_data = [
                ["Alumno:", al_data["nombre"], "DNI:", al_data["dni"]],
                ["Sede:", al_data["sede"], "Teléfono:", al_data["telefono"]],
                ["Estado:", al_data["estado"], "Email:", al_data["email"]],
            ]
            info_table = Table(info_data, colWidths=[3 * cm, 7 * cm, 3 * cm, 4 * cm])
            info_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(info_table)
            story.append(Spacer(1, 0.7 * cm))

            # Cuotas table
            if cuotas_pdf:
                header = ["Curso", "Tipo", "Período", "Vencimiento", "Monto", "Saldo", "Estado"]
                table_data = [header] + [
                    [
                        c["curso"][:22],
                        c["tipo"],
                        c["periodo"],
                        c["vencimiento"],
                        c["monto"],
                        c["saldo"],
                        c["estado"],
                    ]
                    for c in cuotas_pdf
                ]
                col_widths = [4.5 * cm, 2 * cm, 2 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2 * cm]
                cuota_table = Table(table_data, colWidths=col_widths, repeatRows=1)
                cuota_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E4057")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(cuota_table)
            else:
                story.append(Paragraph("Sin cuotas para mostrar.", styles["Normal"]))

            story.append(Spacer(1, 0.8 * cm))

            # Total deuda
            total_style = ParagraphStyle(
                "total", parent=styles["Normal"],
                fontSize=12, fontName="Helvetica-Bold",
            )
            story.append(Paragraph(
                f"Total pendiente de pago: <b>{fmt_moneda(al_data['total_deuda'])}</b>",
                total_style,
            ))
            story.append(Spacer(1, 0.5 * cm))
            story.append(Paragraph(
                "Este documento es informativo. No tiene validez como comprobante de pago.",
                ParagraphStyle("footer", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
            ))

            doc.build(story)
            buf_pdf.seek(0)

            fname_pdf = f"estado_cuenta_{al.apellido.lower()}_{al.dni}_{HOY.isoformat()}.pdf"
            st.download_button(
                "⬇️ Descargar PDF",
                data=buf_pdf,
                file_name=fname_pdf,
                mime="application/pdf",
            )
            st.caption(f"Total deuda: **{fmt_moneda(al_data['total_deuda'])}** — {len(cuotas_pdf)} cuota(s) incluidas.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB: MOROSOS EXCEL
# ══════════════════════════════════════════════════════════════════════════════
with tab_mor:
    st.subheader("Reporte de morosos")

    with get_session() as s:
        sedes_m = s.query(Sede).filter(Sede.activa == True).order_by(Sede.nombre).all()
        sede_opts_m = {"Todas": None} | {s2.nombre: s2.id for s2 in sedes_m}

    mc1, mc2 = st.columns([2, 4])
    m_sede_label = mc1.selectbox("Sede", list(sede_opts_m.keys()), key="mor_rep_sede")
    m_sede_id = sede_opts_m[m_sede_label]
    m_venc_hasta = mc2.date_input(
        "Cuotas vencidas hasta", value=HOY, format="DD/MM/YYYY", key="mor_hasta"
    )

    if st.button("📋 Generar Excel morosos", key="btn_mor_excel", type="primary"):
        with get_session() as s:
            q = (
                s.query(Alumno, Cuota, Sede)
                .join(Inscripcion, Alumno.id == Inscripcion.alumno_id)
                .join(Cuota, Inscripcion.id == Cuota.inscripcion_id)
                .join(Sede, Alumno.sede_id == Sede.id)
                .filter(
                    Cuota.fecha_vencimiento <= m_venc_hasta,
                    Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
                    Inscripcion.activa == True,
                )
            )
            if m_sede_id:
                q = q.filter(Alumno.sede_id == m_sede_id)
            rows = q.order_by(Alumno.apellido, Alumno.nombre, Cuota.fecha_vencimiento).all()

        # Resumen por alumno
        from collections import defaultdict
        agrup: dict[int, dict] = {}
        detalle_mor = []
        for alumno, cuota, sede in rows:
            if alumno.id not in agrup:
                agrup[alumno.id] = {
                    "Alumno": f"{alumno.apellido}, {alumno.nombre}",
                    "DNI": alumno.dni,
                    "Sede": sede.nombre,
                    "Teléfono": alumno.telefono or "",
                    "Cuotas vencidas": 0,
                    "Total adeudado": Decimal("0"),
                    "Vencimiento más antiguo": cuota.fecha_vencimiento,
                }
            agrup[alumno.id]["Cuotas vencidas"] += 1
            agrup[alumno.id]["Total adeudado"] += cuota.saldo_pendiente
            if cuota.fecha_vencimiento < agrup[alumno.id]["Vencimiento más antiguo"]:
                agrup[alumno.id]["Vencimiento más antiguo"] = cuota.fecha_vencimiento

            tipo_label = {"matricula": "Matrícula", "mensual": "Cuota mensual", "examen": "Examen"}.get(
                cuota.tipo.value, cuota.tipo.value
            )
            dias = (HOY - cuota.fecha_vencimiento).days
            detalle_mor.append({
                "Alumno": f"{alumno.apellido}, {alumno.nombre}",
                "DNI": alumno.dni,
                "Sede": sede.nombre,
                "Tipo cuota": tipo_label,
                "Período": cuota.periodo or "—",
                "Vencimiento": fmt_fecha(cuota.fecha_vencimiento),
                "Días vencida": dias,
                "Saldo": float(cuota.saldo_pendiente),
            })

        resumen_mor = [
            {
                **v,
                "Total adeudado": float(v["Total adeudado"]),
                "Vencimiento más antiguo": fmt_fecha(v["Vencimiento más antiguo"]),
            }
            for v in sorted(agrup.values(), key=lambda x: x["Total adeudado"], reverse=True)
        ]

        buf_mor = BytesIO()
        with pd.ExcelWriter(buf_mor, engine="openpyxl") as writer:
            pd.DataFrame(resumen_mor).to_excel(writer, sheet_name="Resumen morosos", index=False)
            pd.DataFrame(detalle_mor).to_excel(writer, sheet_name="Detalle cuotas", index=False)
        buf_mor.seek(0)

        fname_mor = f"morosos_{m_venc_hasta.isoformat()}.xlsx"
        st.download_button(
            "⬇️ Descargar Excel",
            data=buf_mor,
            file_name=fname_mor,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        total_mor = sum(r["Total adeudado"] for r in resumen_mor)
        st.caption(f"{len(resumen_mor)} alumnos morosos · Total adeudado: **{fmt_moneda(total_mor)}**")


# ══════════════════════════════════════════════════════════════════════════════
# TAB: BACKUP
# ══════════════════════════════════════════════════════════════════════════════
with tab_backup:
    st.subheader("Backup de base de datos")

    db_path = DB_PATH
    db_size_kb = round(db_path.stat().st_size / 1024, 1) if db_path.exists() else 0

    st.info(
        f"Base de datos: `{db_path.name}`  ·  "
        f"Tamaño: **{db_size_kb} KB**  ·  "
        f"Ruta: `{db_path}`"
    )

    col_dl, col_bk = st.columns(2)

    with col_dl:
        st.markdown("**Descargar copia del archivo**")
        if db_path.exists():
            with open(db_path, "rb") as f:
                db_bytes = f.read()
            fname_db = f"instituto_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            st.download_button(
                "⬇️ Descargar .db",
                data=db_bytes,
                file_name=fname_db,
                mime="application/octet-stream",
            )
            st.caption("Para restaurar: reemplazá `data/instituto.db` con este archivo y reiniciá la app.")
        else:
            st.error("Archivo de base de datos no encontrado.")

    with col_bk:
        st.markdown("**Guardar copia local en `/data/backups/`**")
        if st.button("💾 Crear backup local", key="btn_backup_local"):
            backup_dir = db_path.parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = backup_dir / f"instituto_{ts}.db"
            shutil.copy2(db_path, dest)
            st.success(f"Backup guardado: `{dest.name}`")

        # List existing backups
        backup_dir = db_path.parent / "backups"
        backups = sorted(backup_dir.glob("*.db"), reverse=True) if backup_dir.exists() else []
        if backups:
            st.caption(f"{len(backups)} backup(s) locales:")
            for b in backups[:5]:
                size = round(b.stat().st_size / 1024, 1)
                st.write(f"• `{b.name}` — {size} KB")
            if len(backups) > 5:
                st.caption(f"... y {len(backups) - 5} más en `data/backups/`")
