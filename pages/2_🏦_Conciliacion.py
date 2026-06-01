from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import (
    Alumno, Cuota, EstadoAlumnoEnum, EstadoCuotaEnum,
    EstadoMovimientoEnum, Inscripcion, MovimientoBancario,
)
from db.session import get_session
from services.bank_import import import_movimientos
from services.reconciliation import (
    aplicar_conciliacion,
    fifo_distribuir,
    guardar_alias_cobro,
    revertir_conciliacion,
    sugerir_conciliacion,
)
from utils.audit import log_action
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Conciliación", page_icon="🏦", layout="wide")
require_login()
st.title("🏦 Conciliación Bancaria")

# ── Session state ──────────────────────────────────────────────────────────────
for k, v in [("conc_msg", None), ("conc_err", None), ("upload_key", 0)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.conc_msg:
    st.success(st.session_state.conc_msg)
    st.session_state.conc_msg = None
if st.session_state.conc_err:
    st.error(st.session_state.conc_err)
    st.session_state.conc_err = None


# ── Upload ─────────────────────────────────────────────────────────────────────
with st.expander("📥 Importar extracto bancario"):
    st.caption(
        "Formatos aceptados:  \n"
        "• **XLS/TSV** — archivo `descargaUltimosMovimientos.xls` del homebanking (Santander, encoding latin-1)  \n"
        "• **PDF** — resumen de cuenta mensual del homebanking Santander  \n"
        "Importación idempotente: subir el mismo archivo dos veces no duplica movimientos."
    )
    uploaded = st.file_uploader(
        "Subir extracto",
        type=["xls", "xlsx", "txt", "tsv", "csv", "pdf"],
        key=f"extracto_upload_{st.session_state.upload_key}",
    )
    if uploaded:
        content = uploaded.read()
        with get_session() as s:
            imported, skipped = import_movimientos(s, content, filename=uploaded.name)
            log_action(s, "IMPORT_EXTRACTO", "movimiento_bancario", None, {
                "archivo": uploaded.name, "importados": imported, "duplicados": skipped,
            })
        st.session_state.upload_key += 1
        st.session_state.conc_msg = f"✅ {imported} importados. Ya existían: {skipped}."
        st.rerun()


# ── Loaders ────────────────────────────────────────────────────────────────────
def load_stats():
    with get_session() as s:
        total = s.query(MovimientoBancario).count()
        pend = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado.in_([
                EstadoMovimientoEnum.pendiente, EstadoMovimientoEnum.parcial,
            ])
        ).count()
        conc = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado == EstadoMovimientoEnum.conciliado
        ).count()
        ign = s.query(MovimientoBancario).filter(
            MovimientoBancario.estado == EstadoMovimientoEnum.ignorado_no_alumno
        ).count()
        return total, pend, conc, ign


def _mov_rows(estados=None):
    with get_session() as s:
        q = s.query(MovimientoBancario)
        if estados:
            q = q.filter(MovimientoBancario.estado.in_(estados))
        rows = q.order_by(MovimientoBancario.fecha.desc()).all()
        result = []
        for r in rows:
            alumno_nombre = "—"
            if r.pagos:
                try:
                    al = r.pagos[0].imputaciones[0].cuota.inscripcion.alumno
                    alumno_nombre = f"{al.apellido}, {al.nombre}"
                except Exception:
                    pass
            metodo_pago = "—"
            if r.pagos:
                try:
                    metodo_pago = r.pagos[0].observaciones or "—"
                except Exception:
                    pass
            result.append({
                "id": r.id,
                "Fecha": fmt_fecha(r.fecha),
                "Pagador": r.nombre_pagador_detectado or "—",
                "Alumno": alumno_nombre,
                "CUIT": r.cuit_detectado or "—",
                "Monto": fmt_moneda(r.importe),
                "importe": r.importe,
                "Tipo": (r.tipo_movimiento or "—")[:35],
                "Estado": r.estado.value,
                "concepto_raw": r.concepto_raw or "",
                "referencia": r.referencia_detectada or "—",
                "metodo_conc": metodo_pago,
            })
        return result


# ── KPIs ───────────────────────────────────────────────────────────────────────
total, pend, conc, ign = load_stats()
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total", total)
k2.metric("Pendientes / Parciales", pend)
k3.metric("Conciliados", conc)
k4.metric("Ignorados", ign)
st.divider()


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_conc, tab_ok, tab_ign, tab_todos, tab_imports = st.tabs([
    "⚡ Conciliar",
    "✅ Conciliados",
    "🚫 Ignorados",
    "📋 Todos",
    "🗑️ Importaciones",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB: CONCILIAR
# ══════════════════════════════════════════════════════════════════════════════
with tab_conc:
    movs = _mov_rows([EstadoMovimientoEnum.pendiente, EstadoMovimientoEnum.parcial])

    if not movs:
        st.info("Sin movimientos pendientes. Importá un extracto.")
        st.stop()

    # ── Batch approve alto ────────────────────────────────────────────────────
    with st.expander("⚡ Aprobar en lote — confianza ALTO (CUIT único)"):
        if st.button("Calcular candidatos", key="batch_calc"):
            with get_session() as s:
                candidatos = []
                for m in movs:
                    sug = sugerir_conciliacion(s, m["id"])
                    if sug.confianza == "alto" and not sug.es_no_alumno and sug.imputaciones:
                        candidatos.append({
                            "mov_id": m["id"],
                            "label": f"{m['Fecha']} | {m['Pagador']} | {m['Monto']}",
                            "imputaciones": [
                                {"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
                                for i in sug.imputaciones
                            ],
                        })
            st.session_state.batch_cands = candidatos

        if "batch_cands" in st.session_state:
            cands = st.session_state.batch_cands
            n = len(cands)
            if n == 0:
                st.info("Sin candidatos de confianza ALTO.")
            else:
                st.caption("Candidatos encontrados:")
                for c in cands:
                    st.write(f"• {c['label']}")
                st.warning(f"{n} movimientos serán aprobados automáticamente.")
                if st.button(f"✅ Confirmar y aprobar {n}", key="batch_go", type="primary"):
                    ok = err = 0
                    with get_session() as s:
                        for c in cands:
                            try:
                                aplicar_conciliacion(s, c["mov_id"], c["imputaciones"], "batch_alto")
                                ok += 1
                            except Exception:
                                err += 1
                    del st.session_state.batch_cands
                    st.session_state.conc_msg = f"✅ Aprobados: {ok}. Errores: {err}."
                    st.rerun()

    st.divider()

    # ── Selector + detail ──────────────────────────────────────────────────────
    col_sel, col_det = st.columns([2, 3])

    with col_sel:
        st.subheader(f"Movimientos pendientes de conciliar ({len(movs)})")
        opts = {
            (
                f"[{m['Fecha']}] {m['Pagador'][:20]}"
                + (f" | CUIT {m['CUIT']}" if m['CUIT'] != "—" else "")
                + f" | {m['Monto']}"
            ): m["id"]
            for m in movs
        }
        sel_label = st.selectbox(
            "Seleccioná movimiento",
            options=list(opts.keys()),
            label_visibility="collapsed",
            key="mov_sel",
        )
        mov_id = opts.get(sel_label)

    with col_det:
        if not mov_id:
            st.info("Seleccioná un movimiento.")
        else:
            with get_session() as s:
                mov = s.get(MovimientoBancario, mov_id)
                mdat = {
                    "monto": fmt_moneda(mov.importe),
                    "importe": mov.importe,
                    "fecha": fmt_fecha(mov.fecha),
                    "pagador": mov.nombre_pagador_detectado or "—",
                    "cuit": mov.cuit_detectado or "—",
                    "ref": mov.referencia_detectada or "—",
                    "tipo": (mov.tipo_movimiento or "—")[:50],
                    "subtipo": mov.subtipo or "—",
                    "estado": mov.estado.value,
                    "concepto": mov.concepto_raw or "",
                }
                sug = sugerir_conciliacion(s, mov_id)

            ca, cb, cc, cd = st.columns(4)
            ca.metric("Monto", mdat["monto"])
            cb.metric("Fecha", mdat["fecha"])
            cc.metric("CUIT", mdat["cuit"])
            cd.metric("Estado", mdat["estado"])

            da, db = st.columns(2)
            da.markdown(f"**Pagador:** {mdat['pagador']}")
            db.markdown(f"**Referencia:** {mdat['ref']}")
            ea, eb = st.columns(2)
            ea.markdown(f"**Tipo mov.:** {mdat['tipo']}")
            eb.markdown(f"**Subtipo:** {mdat['subtipo']}")

            with st.expander("📄 Concepto completo del extracto"):
                st.code(mdat["concepto"])

            st.divider()

            # ── Suggestion ──────────────────────────────────────────────────
            if sug.sin_match:
                st.warning("⚪ Sin match automático — usá búsqueda manual abajo.")

            elif sug.es_no_alumno:
                st.info("🏢 Detectado como **no alumno** (patrón proveedor).")
                if st.button("🚫 Confirmar como ignorado", key=f"ign_noal_{mov_id}"):
                    with get_session() as s:
                        m = s.get(MovimientoBancario, mov_id)
                        m.estado = EstadoMovimientoEnum.ignorado_no_alumno
                        log_action(s, "IGNORAR", "movimiento_bancario", mov_id, {})
                    st.session_state.conc_msg = "Movimiento ignorado."
                    st.rerun()

            else:
                badge = {"alto": "🟢 ALTO", "medio": "🟡 MEDIO", "bajo": "🔴 BAJO"}.get(sug.confianza, "⚪")
                st.write(f"**Confianza:** {badge}  ·  **Método:** `{sug.metodo}`")

                if sug.imputaciones:
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Alumno": i.alumno_nombre,
                                "Cuota": i.cuota_descripcion,
                                "A imputar": fmt_moneda(i.monto_a_imputar),
                                "Saldo tras pago": fmt_moneda(i.saldo_restante_cuota),
                            }
                            for i in sug.imputaciones
                        ]),
                        use_container_width=True, hide_index=True,
                    )
                    if sug.excedente > Decimal("0.00"):
                        st.caption(f"Excedente no imputado: {fmt_moneda(sug.excedente)}")

                    # ── Alias para conciliaciones futuras ───────────────
                    _al_ids_sug = list({i.alumno_id for i in sug.imputaciones})
                    _mostrar_alias = len(_al_ids_sug) == 1 and mdat["pagador"] != "—"
                    if _mostrar_alias:
                        _alias_chk = st.checkbox(
                            "Guardar nombre del pagador como referencia futura",
                            value=True, key=f"alias_chk_{mov_id}",
                        )
                        _alias_txt = st.text_input(
                            "Alias", value=mdat["pagador"], key=f"alias_txt_{mov_id}",
                        )

                    ba, bb = st.columns(2)
                    if ba.button("✅ Aprobar sugerencia", key=f"apro_{mov_id}", type="primary"):
                        with get_session() as s:
                            aplicar_conciliacion(
                                s, mov_id,
                                [{"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
                                 for i in sug.imputaciones],
                                operador="manual",
                            )
                            if _mostrar_alias and st.session_state.get(f"alias_chk_{mov_id}"):
                                alias_val = st.session_state.get(f"alias_txt_{mov_id}", "").strip()
                                if alias_val:
                                    guardar_alias_cobro(s, _al_ids_sug[0], alias_val)
                        st.session_state.conc_msg = "✅ Conciliación aprobada."
                        st.rerun()
                    if bb.button("🚫 Ignorar movimiento", key=f"ign_{mov_id}"):
                        with get_session() as s:
                            m = s.get(MovimientoBancario, mov_id)
                            m.estado = EstadoMovimientoEnum.ignorado_no_alumno
                            log_action(s, "IGNORAR", "movimiento_bancario", mov_id, {})
                        st.session_state.conc_msg = "Movimiento ignorado."
                        st.rerun()
                else:
                    st.warning("Match encontrado pero sin cuotas pendientes.")
                    if st.button("🚫 Ignorar movimiento", key=f"ign2_{mov_id}"):
                        with get_session() as s:
                            m = s.get(MovimientoBancario, mov_id)
                            m.estado = EstadoMovimientoEnum.ignorado_no_alumno
                            log_action(s, "IGNORAR", "movimiento_bancario", mov_id, {})
                        st.session_state.conc_msg = "Movimiento ignorado."
                        st.rerun()

            # ── Manual override ──────────────────────────────────────────────
            st.divider()
            with st.expander("🔍 Asignar manualmente a otro alumno"):
                with get_session() as s:
                    al_list = (
                        s.query(Alumno)
                        .filter(Alumno.estado == EstadoAlumnoEnum.activo)
                        .order_by(Alumno.apellido, Alumno.nombre)
                        .all()
                    )
                    al_opts = {
                        f"{a.apellido}, {a.nombre} — DNI {a.dni}": a.id
                        for a in al_list
                    }

                if not al_opts:
                    st.info("Sin alumnos activos.")
                else:
                    al_sel_label = st.selectbox(
                        "Alumno", list(al_opts.keys()), key=f"al_man_{mov_id}"
                    )
                    al_id = al_opts[al_sel_label]

                    with get_session() as s:
                        imps_man, exc_man = fifo_distribuir(s, [al_id], mdat["importe"])
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
                        cuotas_df_data = [
                            {
                                "Tipo": c.tipo.value,
                                "Período": c.periodo or "—",
                                "Vence": fmt_fecha(c.fecha_vencimiento),
                                "Saldo": fmt_moneda(c.saldo_pendiente),
                            }
                            for c in cuotas_pend
                        ]

                    if cuotas_df_data:
                        st.caption(f"Cuotas pendientes:")
                        st.dataframe(pd.DataFrame(cuotas_df_data), use_container_width=True, hide_index=True)

                    if imps_man:
                        st.caption(f"Distribución FIFO propuesta ({mdat['monto']}):")
                        st.dataframe(
                            pd.DataFrame([
                                {"Cuota": i.cuota_descripcion, "A imputar": fmt_moneda(i.monto_a_imputar)}
                                for i in imps_man
                            ]),
                            use_container_width=True, hide_index=True,
                        )
                        if exc_man > Decimal("0.00"):
                            st.caption(f"Excedente: {fmt_moneda(exc_man)}")

                        # ── Alias para conciliaciones futuras ───────────
                        _man_mostrar_alias = mdat["pagador"] != "—"
                        if _man_mostrar_alias:
                            _man_alias_chk = st.checkbox(
                                "Guardar nombre del pagador como referencia futura",
                                value=True, key=f"man_alias_chk_{mov_id}",
                            )
                            _man_alias_txt = st.text_input(
                                "Alias", value=mdat["pagador"], key=f"man_alias_txt_{mov_id}",
                            )

                        if st.button(
                            "✅ Aplicar imputación manual",
                            key=f"apply_man_{mov_id}",
                            type="primary",
                        ):
                            with get_session() as s:
                                aplicar_conciliacion(
                                    s, mov_id,
                                    [{"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
                                     for i in imps_man],
                                    operador="manual_override",
                                )
                                if _man_mostrar_alias and st.session_state.get(f"man_alias_chk_{mov_id}"):
                                    alias_val = st.session_state.get(f"man_alias_txt_{mov_id}", "").strip()
                                    if alias_val:
                                        guardar_alias_cobro(s, al_id, alias_val)
                            st.session_state.conc_msg = "✅ Imputación manual aplicada."
                            st.rerun()
                    else:
                        st.info("Sin cuotas pendientes — no se puede imputar.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB: CONCILIADOS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ok:
    movs_ok = _mov_rows([EstadoMovimientoEnum.conciliado])

    _METODO_LABELS = {
        "batch_alto":     "🟢 Auto-CUIT",
        "manual":         "🟡 Confirmado",
        "manual_override":"🔵 Manual",
        "—":              "—",
    }

    with st.expander("📖 Glosario — métodos de conciliación"):
        st.markdown("""
| Símbolo | Método | Descripción |
|---------|--------|-------------|
| 🟢 **Auto-CUIT** | `batch_alto` | El CUIT del pagador coincide exactamente con un referente registrado. Confianza ALTA. Aprobado en lote sin intervención. |
| 🟡 **Confirmado** | `manual` | El sistema sugirió un alumno (por CUIT, alias o nombre fuzzy) y el usuario lo confirmó manualmente. |
| 🔵 **Manual** | `manual_override` | El usuario ignoró la sugerencia automática y seleccionó el alumno a mano. |
| 🟠 **Alias** | interno | El nombre del pagador coincide con un alias guardado previamente (≥85% similitud). Confianza ALTA. |
| 🟡 **Fuzzy nombre** | interno | El nombre del pagador se parece al nombre del referente (≥80% similitud). Confianza MEDIA. |
| 🔴 **Fuzzy referencia** | interno | La referencia del concepto bancario se parece al nombre del alumno (≥75% similitud). Confianza BAJA. |
        """)

    if not movs_ok:
        st.info("Sin movimientos conciliados aún.")
    else:
        st.caption(f"{len(movs_ok)} conciliados — ↩️ para revertir")
        hc = st.columns([2, 3, 3, 2, 2, 1])
        for col, lbl in zip(hc, ["Fecha", "Pagador", "Alumno", "Monto", "Método", ""]):
            col.markdown(f"**{lbl}**")
        for m in movs_ok:
            c1, c2, c3, c4, c5, c6 = st.columns([2, 3, 3, 2, 2, 1])
            c1.write(m["Fecha"])
            c2.write(m["Pagador"])
            c3.write(m["Alumno"])
            c4.write(m["Monto"])
            c5.write(_METODO_LABELS.get(m["metodo_conc"], m["metodo_conc"]))
            if c6.button("↩️", key=f"rev_{m['id']}", help="Revertir conciliación"):
                with get_session() as s:
                    revertir_conciliacion(s, m["id"])
                st.session_state.conc_msg = "Conciliación revertida."
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB: IGNORADOS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ign:
    movs_ign = _mov_rows([EstadoMovimientoEnum.ignorado_no_alumno])

    if not movs_ign:
        st.info("Sin movimientos ignorados.")
    else:
        st.caption(f"{len(movs_ign)} ignorados — ↩️ para volver a pendiente")
        for m in movs_ign:
            c1, c2, c3, c4 = st.columns([2, 4, 2, 1])
            c1.write(m["Fecha"])
            c2.write((m["concepto_raw"][:55] if m["concepto_raw"] else m["Pagador"]))
            c3.write(m["Monto"])
            if c4.button("↩️", key=f"unign_{m['id']}", help="Volver a pendiente"):
                with get_session() as s:
                    mv = s.get(MovimientoBancario, m["id"])
                    mv.estado = EstadoMovimientoEnum.pendiente
                    log_action(s, "DESIGNORAR", "movimiento_bancario", m["id"], {})
                st.session_state.conc_msg = "Movimiento vuelto a pendiente."
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB: TODOS
# ══════════════════════════════════════════════════════════════════════════════
with tab_todos:
    todos = _mov_rows()
    if todos:
        st.dataframe(
            pd.DataFrame([
                {k: v for k, v in m.items()
                 if k not in ("id", "importe", "concepto_raw", "referencia")}
                for m in todos
            ]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("Sin movimientos importados.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB: IMPORTACIONES
# ══════════════════════════════════════════════════════════════════════════════
def _load_importaciones():
    """Return list of import batches grouped by fecha_importacion (minute precision)."""
    with get_session() as s:
        movs = (
            s.query(MovimientoBancario)
            .order_by(MovimientoBancario.fecha_importacion.desc())
            .all()
        )
        batches: dict[str, dict] = {}
        for m in movs:
            key = m.fecha_importacion.strftime("%Y-%m-%d %H:%M") if m.fecha_importacion else "Sin fecha"
            if key not in batches:
                batches[key] = {
                    "key": key,
                    "ids": [],
                    "pendiente": 0,
                    "conciliado": 0,
                    "parcial": 0,
                    "ignorado": 0,
                }
            batches[key]["ids"].append(m.id)
            estado = m.estado.value
            if estado in batches[key]:
                batches[key][estado] += 1
        return list(batches.values())


def _eliminar_importacion(mov_ids: list[int]) -> tuple[int, int]:
    """
    Delete a batch of MovimientoBancario by ids.
    Reverts conciliated movements first (restores cuota states).
    Returns (deleted, reverted).
    """
    from db.models import Cuota, EstadoCuotaEnum, Imputacion
    reverted = deleted = 0
    with get_session() as s:
        for mid in mov_ids:
            m = s.get(MovimientoBancario, mid)
            if not m:
                continue
            # Revert any pagos attached
            for pago in list(m.pagos):
                for imp in list(pago.imputaciones):
                    cuota = s.get(Cuota, imp.cuota_id)
                    if cuota:
                        cuota.saldo_pendiente += imp.monto_imputado
                        cuota.estado = (
                            EstadoCuotaEnum.pendiente
                            if cuota.saldo_pendiente >= cuota.monto_actualizado
                            else EstadoCuotaEnum.parcial
                        )
                    s.delete(imp)
                s.flush()
                s.delete(pago)
                reverted += 1
            s.flush()
            s.delete(m)
            deleted += 1
    return deleted, reverted


with tab_imports:
    importaciones = _load_importaciones()

    if not importaciones:
        st.info("Sin importaciones registradas.")
    else:
        st.caption(f"{len(importaciones)} lote(s) importado(s). Eliminar un lote revierte las conciliaciones incluidas.")

        for batch in importaciones:
            total = len(batch["ids"])
            conc = batch["conciliado"] + batch["parcial"]
            label = (
                f"📅 {batch['key']} — {total} movimientos "
                f"({batch['pendiente']} pend · {conc} conc · {batch['ignorado']} ign)"
            )
            with st.expander(label):
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Total", total)
                mc2.metric("Pendientes", batch["pendiente"])
                mc3.metric("Conciliados", conc)
                mc4.metric("Ignorados", batch["ignorado"])

                if conc > 0:
                    st.warning(
                        f"⚠️ Este lote tiene **{conc} movimientos conciliados**. "
                        f"Al eliminar se revertirán los cobros y las cuotas volverán a pendiente."
                    )

                _confirm_key = f"confirm_del_{batch['key']}"
                _btn_key = f"del_batch_{batch['key']}"

                if st.checkbox("Confirmo que quiero eliminar este lote", key=_confirm_key):
                    if st.button("🗑️ Eliminar lote", key=_btn_key, type="secondary"):
                        deleted, reverted = _eliminar_importacion(batch["ids"])
                        st.session_state.conc_msg = (
                            f"Lote eliminado: {deleted} movimientos borrados"
                            + (f", {reverted} cobros revertidos." if reverted else ".")
                        )
                        st.rerun()
