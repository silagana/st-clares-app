from datetime import date
from decimal import Decimal
from io import BytesIO

import pandas as pd
import streamlit as st

from db.models import (
    AliasCobroAlumno, Alumno, Curso, EstadoAlumnoEnum, EstadoCuotaEnum,
    Imputacion, Inscripcion, Pago, ReferentePago, Sede,
)
from db.session import get_session
from services.enrollment import (
    actualizar_monto_cuota,
    actualizar_monto_cuotas_alumno,
    confirmar_inscripcion_provisional,
    dar_de_baja_inscripcion,
    eliminar_cuota,
    generate_cuotas,
    generate_cuotas_periodo,
    generate_inscripcion_provisional,
    generate_matricula_only,
    recalculate_cuotas_by_inscripcion,
)
from utils.audit import log_action
from utils.auth import require_login
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Alumnos", page_icon="👥", layout="wide")
require_login()
st.title("👥 Alumnos")

for k, v in [("al_id", None), ("al_msg", None), ("show_new_al", False)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.al_msg:
    st.success(st.session_state.al_msg)
    st.session_state.al_msg = None


# ── Loaders ────────────────────────────────────────────────────────────────

def load_sedes_map():
    with get_session() as s:
        return {r.nombre: r.id for r in s.query(Sede).filter(Sede.activa == True).order_by(Sede.nombre)}


def load_cursos_activos():
    with get_session() as s:
        rows = s.query(Curso).filter(Curso.activo == True).order_by(Curso.nombre).all()
        return [{"id": r.id, "label": f"{r.nombre} ({r.sede.nombre})"} for r in rows]


def load_alumnos(sede_id=None, estado=None, search=None):
    with get_session() as s:
        q = s.query(Alumno)
        if sede_id:
            q = q.filter(Alumno.sede_id == sede_id)
        if estado:
            q = q.filter(Alumno.estado == estado)
        rows = q.order_by(Alumno.apellido, Alumno.nombre).all()
        result = []
        for r in rows:
            full = f"{r.apellido}, {r.nombre}"
            if search and search.lower() not in full.lower() and search not in (r.dni or ""):
                continue
            result.append({
                "id": r.id,
                "Alumno": full,
                "DNI": r.dni,
                "Sede": r.sede.nombre if r.sede else "",
                "Estado": r.estado.value,
                "Tel": r.telefono or "",
            })
        return result


def load_alumno_detail(alumno_id):
    with get_session() as s:
        r = s.get(Alumno, alumno_id)
        if not r:
            return None
        referentes = [
            {
                "id": ref.id,
                "nombre_completo": ref.nombre_completo,
                "cuit_cuil": ref.cuit_cuil,
                "telefono": ref.telefono or "",
                "vinculo": ref.vinculo.value,
                "es_default": ref.es_default,
            }
            for ref in r.referentes
        ]
        aliases = [{"id": a.id, "alias": a.alias} for a in r.aliases_cobro]
        inscripciones = []
        for ins in r.inscripciones:
            cuotas_pendientes = [
                c for c in ins.cuotas
                if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial)
            ]
            inscripciones.append({
                "id": ins.id,
                "curso_id": ins.curso_id,
                "curso": ins.curso.nombre,
                "sede": ins.curso.sede.nombre,
                "fecha": ins.fecha_inscripcion,
                "descuento_pct": float(ins.descuento_porcentaje) if ins.descuento_porcentaje else 0.0,
                "descuento_fijo": float(ins.descuento_fijo) if ins.descuento_fijo else 0.0,
                "activa": ins.activa,
                "provisional": ins.provisional,
                "anio_reserva": ins.anio_reserva,
                "cuotas_total": len(ins.cuotas),
                "cuotas_impagas": len(cuotas_pendientes),
                "deuda_total": sum(float(c.saldo_pendiente) for c in cuotas_pendientes),
                "tiene_matricula": any(c.tipo.value == "matricula" for c in ins.cuotas),
            })
        cuotas_ec = []
        for ins in r.inscripciones:
            for c in sorted(ins.cuotas, key=lambda x: x.fecha_vencimiento):
                dias_atraso = 0
                if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                    dias_atraso = max(0, (date.today() - c.fecha_vencimiento).days)
                cuotas_ec.append({
                    "_id": c.id,
                    "_monto_float": float(c.monto_actualizado),
                    "_saldo_float": float(c.saldo_pendiente),
                    "_tiene_imp": len(c.imputaciones) > 0,
                    "_estado_raw": c.estado.value,
                    "Curso": ins.curso.nombre + (" [PROV]" if ins.provisional else ""),
                    "Tipo": c.tipo.value,
                    "Período": c.periodo or "-",
                    "Vencimiento": fmt_fecha(c.fecha_vencimiento),
                    "Monto": fmt_moneda(c.monto_actualizado),
                    "Saldo": fmt_moneda(c.saldo_pendiente),
                    "Estado": c.estado.value,
                    "Días atraso": dias_atraso,
                })
        all_cuota_ids = [c["_id"] for c in cuotas_ec]
        pagos_ec = []
        if all_cuota_ids:
            pagos_q = (
                s.query(Pago)
                .join(Imputacion, Pago.id == Imputacion.pago_id)
                .filter(Imputacion.cuota_id.in_(all_cuota_ids))
                .distinct()
                .order_by(Pago.fecha.asc(), Pago.id.asc())
                .all()
            )
            for pago in pagos_q:
                pagador = "—"
                if pago.medio.value == "transferencia" and pago.movimiento:
                    pagador = pago.movimiento.nombre_pagador_detectado or "—"
                pagos_ec.append({
                    "Fecha": fmt_fecha(pago.fecha),
                    "Monto": fmt_moneda(pago.monto),
                    "Forma": pago.medio.value,
                    "Pagador": pagador,
                    "_monto_float": float(pago.monto),
                })

        return {
            "id": r.id,
            "nombre": r.nombre,
            "apellido": r.apellido,
            "dni": r.dni,
            "telefono": r.telefono or "",
            "email": r.email or "",
            "sede_id": r.sede_id,
            "estado": r.estado.value,
            "fecha_nacimiento": r.fecha_nacimiento,
            "motivo_estado": r.motivo_estado or "",
            "observaciones": r.observaciones or "",
            "referentes": referentes,
            "aliases": aliases,
            "inscripciones": inscripciones,
            "cuotas_ec": cuotas_ec,
            "pagos_ec": pagos_ec,
        }


# ── Filtros + tabla ────────────────────────────────────────────────────────

sedes_map = load_sedes_map()

c1, c2, c3 = st.columns([3, 1, 1])
search = c1.text_input("🔍 Nombre o DNI", placeholder="García / 30123456")
sede_filter = c2.selectbox("Sede", ["Todas"] + list(sedes_map.keys()))
estado_filter = c3.selectbox("Estado", ["Todos", "activo", "suspendido", "baja"])

sede_id_f = sedes_map.get(sede_filter) if sede_filter != "Todas" else None
estado_f = estado_filter if estado_filter != "Todos" else None

alumnos = load_alumnos(sede_id_f, estado_f, search or None)

_col_list, _col_btn = st.columns([4, 1])
with _col_btn:
    st.write("")  # vertical align
    if st.button("➕ Nuevo alumno", key="btn_nuevo_al", use_container_width=True):
        st.session_state.al_id = None
        st.session_state.show_new_al = True
        st.rerun()

if alumnos:
    with _col_list:
        df = pd.DataFrame(alumnos).drop(columns=["id"])
        st.dataframe(df, use_container_width=True, hide_index=True)
    names = [a["Alumno"] for a in alumnos]
    ids = [a["id"] for a in alumnos]
    sel = st.selectbox(
        "Seleccionar alumno:",
        names,
        index=None,
        placeholder="Escribir para buscar o seleccionar de la lista...",
    )
    if sel is not None:
        new_al_id = ids[names.index(sel)]
        if new_al_id != st.session_state.al_id:
            st.session_state.al_id = new_al_id
            st.session_state.show_new_al = False
            st.rerun()
else:
    st.info("Sin alumnos para estos filtros.")

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────

tab_edit, tab_cuenta, tab_baja, tab_provisional, tab_import = st.tabs([
    "✏️ Alta / Edición",
    "📊 Estado de cuenta",
    "🚫 Baja / Suspensión",
    "⭐ Matrícula Preferencial",
    "📥 Importar Excel",
])

al_id = st.session_state.al_id
detail = load_alumno_detail(al_id) if al_id else None

# ── Tab: Alta / Edición ────────────────────────────────────────────────────

with tab_edit:
    is_new = al_id is None
    d = detail or {}

    _show_form = al_id is not None or st.session_state.show_new_al
    if not _show_form:
        st.info("Seleccioná un alumno de la lista o hacé clic en **➕ Nuevo alumno**.")
    else:
        st.subheader("Nuevo alumno" if is_new else f"Editar: {d.get('apellido', '')}, {d.get('nombre', '')}")

        with st.form("alumno_form"):
            r1c1, r1c2, r1c3 = st.columns(3)
            apellido = r1c1.text_input("Apellido *", value=d.get("apellido", ""))
            nombre = r1c2.text_input("Nombre *", value=d.get("nombre", ""))
            dni = r1c3.text_input("DNI *", value=d.get("dni", ""))

            r2c1, r2c2, r2c3 = st.columns(3)
            telefono = r2c1.text_input("Teléfono", value=d.get("telefono", ""))
            email = r2c2.text_input("Email", value=d.get("email", ""))
            sede_names = list(sedes_map.keys())
            sede_ids_list = list(sedes_map.values())
            cur_idx = sede_ids_list.index(d["sede_id"]) if d.get("sede_id") in sede_ids_list else 0
            sede_sel = r2c3.selectbox("Sede *", sede_names, index=cur_idx)

            fecha_nac = st.date_input(
                "Fecha de nacimiento",
                value=d.get("fecha_nacimiento"),
                format="DD/MM/YYYY",
            )
            observaciones = st.text_area("Observaciones", value=d.get("observaciones", ""), height=60)

            st.markdown("**Referente de pago principal**")
            ref0 = d.get("referentes", [{}])[0] if d.get("referentes") else {}
            rc1, rc2 = st.columns(2)
            ref_nombre = rc1.text_input("Nombre completo", value=ref0.get("nombre_completo", ""))
            ref_cuit = rc2.text_input("CUIT / CUIL", value=ref0.get("cuit_cuil", ""))
            rc3, rc4 = st.columns(2)
            vinculos = ["mismo_alumno", "padre", "madre", "empresa", "otro"]
            cur_vinculo = ref0.get("vinculo", "mismo_alumno")
            ref_vinculo = rc3.selectbox("Vínculo", vinculos,
                                        index=vinculos.index(cur_vinculo) if cur_vinculo in vinculos else 0)
            ref_tel = rc4.text_input("Teléfono referente", value=ref0.get("telefono", ""))

            submit_al = st.form_submit_button("Guardar alumno")

        if submit_al:
            errs = []
            if not apellido.strip(): errs.append("Apellido requerido")
            if not nombre.strip(): errs.append("Nombre requerido")
            if not dni.strip(): errs.append("DNI requerido")
            if not sede_names: errs.append("Primero cargá sedes")

            for e in errs:
                st.error(e)

            if not errs:
                with get_session() as s:
                    if is_new:
                        alumno = Alumno(
                            apellido=apellido.strip(),
                            nombre=nombre.strip(),
                            dni=dni.strip(),
                            telefono=telefono.strip() or None,
                            email=email.strip() or None,
                            sede_id=sedes_map[sede_sel],
                            estado=EstadoAlumnoEnum.activo,
                            fecha_nacimiento=fecha_nac,
                            observaciones=observaciones.strip() or None,
                        )
                        s.add(alumno)
                        s.flush()
                        new_id = alumno.id
                    else:
                        alumno = s.get(Alumno, al_id)
                        alumno.apellido = apellido.strip()
                        alumno.nombre = nombre.strip()
                        alumno.dni = dni.strip()
                        alumno.telefono = telefono.strip() or None
                        alumno.email = email.strip() or None
                        alumno.sede_id = sedes_map[sede_sel]
                        alumno.fecha_nacimiento = fecha_nac
                        alumno.observaciones = observaciones.strip() or None
                        new_id = al_id

                    if ref_nombre.strip() and ref_cuit.strip():
                        existing = s.query(ReferentePago).filter(
                            ReferentePago.alumno_id == new_id
                        ).first()
                        if existing:
                            existing.nombre_completo = ref_nombre.strip()
                            existing.cuit_cuil = ref_cuit.strip()
                            existing.vinculo = ref_vinculo
                            existing.telefono = ref_tel.strip() or None
                            existing.es_default = True
                        else:
                            s.add(ReferentePago(
                                alumno_id=new_id,
                                nombre_completo=ref_nombre.strip(),
                                cuit_cuil=ref_cuit.strip(),
                                vinculo=ref_vinculo,
                                telefono=ref_tel.strip() or None,
                                es_default=True,
                            ))

                    log_action(s, "CREATE" if is_new else "UPDATE", "alumno", new_id,
                               {"apellido": apellido, "nombre": nombre})
                    st.session_state.al_id = new_id
                    st.session_state.show_new_al = False

                st.session_state.al_msg = "Alumno guardado."
                st.rerun()

        # ── Referencias de cobro ───────────────────────────────────────────
        if al_id and detail:
            _aliases = detail.get("aliases", [])
            st.divider()
            st.markdown("**🔗 Referencias de cobro** (para conciliación automática)")
            if _aliases:
                for _a in _aliases:
                    _ac1, _ac2 = st.columns([5, 1])
                    _ac1.code(_a["alias"])
                    if _ac2.button("✕", key=f"del_alias_{_a['id']}", help="Eliminar"):
                        with get_session() as s:
                            _aobj = s.get(AliasCobroAlumno, _a["id"])
                            if _aobj:
                                s.delete(_aobj)
                        st.session_state.al_msg = "Referencia eliminada."
                        st.rerun()
            else:
                st.caption("Sin referencias. Se agregan automáticamente al confirmar cobros en Conciliación.")
            with st.form("add_alias_form"):
                _new_alias_input = st.text_input(
                    "Agregar referencia manualmente",
                    placeholder="nombre tal como aparece en transferencia bancaria",
                )
                if st.form_submit_button("Agregar"):
                    if _new_alias_input.strip():
                        with get_session() as s:
                            from services.reconciliation import guardar_alias_cobro as _gac
                            _gac(s, al_id, _new_alias_input.strip())
                        st.session_state.al_msg = "Referencia agregada."
                        st.rerun()

    # ── Inscripciones ──────────────────────────────────────────────────────
    if al_id and detail:
        st.divider()
        st.subheader("📚 Inscripciones")

        ins_regulares = [i for i in detail["inscripciones"] if not i["provisional"]]
        ins_provisionales = [i for i in detail["inscripciones"] if i["provisional"]]

        if ins_regulares:
            for ins in ins_regulares:
                estado_icon = "✅" if ins["activa"] else "❌"
                label = (
                    f"{estado_icon} {ins['curso']} ({ins['sede']}) — "
                    f"{ins['cuotas_impagas']} impagas — "
                    f"Deuda: {fmt_moneda(ins['deuda_total'])}"
                )
                with st.expander(label):
                    col_info, col_actions = st.columns([2, 3])

                    with col_info:
                        st.caption(f"Inscripto: {fmt_fecha(ins['fecha'])}")
                        st.caption(f"Cuotas: {ins['cuotas_total']} total, {ins['cuotas_impagas']} impagas")

                    with col_actions:
                        # ── Editar descuento ───────────────────────────
                        with st.form(f"desc_form_{ins['id']}"):
                            st.markdown("**Descuento**")
                            da, db = st.columns(2)
                            new_pct = da.number_input(
                                "% descuento", min_value=0.0, max_value=100.0, step=1.0,
                                value=ins["descuento_pct"], key=f"pct_{ins['id']}"
                            )
                            new_fijo = db.number_input(
                                "$ descuento fijo", min_value=0.0, step=100.0,
                                value=ins["descuento_fijo"], key=f"fijo_{ins['id']}"
                            )
                            save_desc = st.form_submit_button("Guardar descuento y recalcular cuotas")

                        if save_desc and ins["activa"]:
                            with get_session() as s:
                                obj = s.get(Inscripcion, ins["id"])
                                obj.descuento_porcentaje = Decimal(str(new_pct)) if new_pct > 0 else None
                                obj.descuento_fijo = Decimal(str(new_fijo)) if new_fijo > 0 else None
                                s.flush()
                                n = recalculate_cuotas_by_inscripcion(s, ins["id"])
                                log_action(s, "DESCUENTO_UPDATE", "inscripcion", ins["id"],
                                           {"pct": new_pct, "fijo": new_fijo, "cuotas_recalc": n})
                            st.session_state.al_msg = f"Descuento actualizado. {n} cuotas recalculadas."
                            st.rerun()

                    if ins["activa"]:
                        st.markdown("---")
                        bc1, bc2, bc3 = st.columns(3)

                        # ── Generar matrícula ──────────────────────────
                        with bc1:
                            st.markdown("**Matrícula**")
                            if ins["tiene_matricula"]:
                                st.caption("Ya tiene matrícula generada.")
                            else:
                                fecha_mat = st.date_input(
                                    "Vencimiento matrícula",
                                    value=date.today(),
                                    format="DD/MM/YYYY",
                                    key=f"fmat_{ins['id']}",
                                )
                                if st.button("Generar matrícula", key=f"btn_mat_{ins['id']}"):
                                    with get_session() as s:
                                        obj = s.get(Inscripcion, ins["id"])
                                        cuota = generate_matricula_only(s, obj, fecha_mat)
                                        if cuota:
                                            log_action(s, "MATRICULA_GENERADA", "inscripcion", ins["id"],
                                                       {"fecha_vto": str(fecha_mat)})
                                            st.session_state.al_msg = "Matrícula generada."
                                        else:
                                            st.session_state.al_msg = "Ya existe matrícula."
                                    st.rerun()

                        # ── Generar año completo ───────────────────────
                        with bc2:
                            st.markdown("**Cuotas anuales**")
                            anio_gen = st.number_input(
                                "Año", min_value=2020, max_value=2040,
                                value=date.today().year, step=1,
                                key=f"anio_{ins['id']}"
                            )
                            if st.button("Generar Mar–Dic (10 cuotas)", key=f"btn_anual_{ins['id']}"):
                                inicio = date(int(anio_gen), 3, 1)
                                with get_session() as s:
                                    obj = s.get(Inscripcion, ins["id"])
                                    cuotas = generate_cuotas_periodo(s, obj, inicio, 10)
                                    log_action(s, "CUOTAS_ANUALES", "inscripcion", ins["id"],
                                               {"anio": anio_gen, "generadas": len(cuotas)})
                                st.session_state.al_msg = (
                                    f"{len(cuotas)} cuotas generadas para {anio_gen}."
                                    if cuotas else "Todos los períodos ya existen."
                                )
                                st.rerun()

                        # ── Dar de baja inscripción ────────────────────
                        with bc3:
                            st.markdown("**Dar de baja**")
                            condonar = st.checkbox(
                                "Condonar cuotas futuras", value=True,
                                key=f"cond_{ins['id']}"
                            )
                            if st.button("🚫 Dar de baja inscripción", key=f"btn_baja_{ins['id']}",
                                         type="secondary"):
                                with get_session() as s:
                                    n_cond = dar_de_baja_inscripcion(s, ins["id"], condonar)
                                    log_action(s, "BAJA_INSCRIPCION", "inscripcion", ins["id"],
                                               {"condonadas": n_cond})
                                st.session_state.al_msg = (
                                    f"Inscripción dada de baja. {n_cond} cuotas condonadas."
                                )
                                st.rerun()
        else:
            st.info("Sin inscripciones activas.")

        # ── Inscripciones provisionales ────────────────────────────────
        if ins_provisionales:
            st.markdown("**Reservas de cupo (provisionales)**")
            for ins in ins_provisionales:
                label_prov = f"⭐ {ins['curso']} — Año {ins['anio_reserva'] or '?'}"
                with st.expander(label_prov):
                    st.caption(f"Cuota de reserva: {fmt_moneda(ins['deuda_total'])}")
                    pc1, pc2 = st.columns(2)
                    fecha_inicio = pc1.date_input(
                        "Inicio cuotas mensuales",
                        value=date(ins["anio_reserva"] or date.today().year, 3, 1),
                        format="DD/MM/YYYY",
                        key=f"fi_prov_{ins['id']}",
                    )
                    cant_c = pc2.number_input(
                        "Cantidad cuotas", min_value=1, max_value=12, value=10,
                        key=f"cant_prov_{ins['id']}"
                    )
                    if st.button("✅ Confirmar inscripción y generar cuotas", key=f"btn_conf_{ins['id']}"):
                        with get_session() as s:
                            cuotas = confirmar_inscripcion_provisional(s, ins["id"], fecha_inicio, int(cant_c))
                            log_action(s, "CONFIRMAR_PROVISIONAL", "inscripcion", ins["id"],
                                       {"cuotas_generadas": len(cuotas)})
                        st.session_state.al_msg = f"Inscripción confirmada. {len(cuotas)} cuotas generadas."
                        st.rerun()
                    if st.button("❌ Cancelar reserva", key=f"btn_cancel_{ins['id']}"):
                        with get_session() as s:
                            dar_de_baja_inscripcion(s, ins["id"], condonar_futuras=True)
                            log_action(s, "CANCELAR_PROVISIONAL", "inscripcion", ins["id"], {})
                        st.session_state.al_msg = "Reserva cancelada."
                        st.rerun()

        st.divider()
        st.subheader("➕ Nueva inscripción")
        cursos_activos = load_cursos_activos()
        if cursos_activos:
            with st.form("ins_form"):
                c_labels = [c["label"] for c in cursos_activos]
                c_ids = [c["id"] for c in cursos_activos]
                c_sel = st.selectbox("Curso *", c_labels)
                ic1, ic2, ic3 = st.columns(3)
                fecha_ins = ic1.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")
                desc_pct = ic2.number_input("Descuento %", min_value=0.0, max_value=100.0, step=1.0)
                desc_fijo = ic3.number_input("Descuento fijo $", min_value=0.0, step=100.0)
                gen_mat = st.checkbox("Generar matrícula al inscribir", value=True)
                submit_ins = st.form_submit_button("Inscribir y generar cuotas")

            if submit_ins:
                cid_sel = c_ids[c_labels.index(c_sel)]
                with get_session() as s:
                    ins = Inscripcion(
                        alumno_id=al_id,
                        curso_id=cid_sel,
                        fecha_inscripcion=fecha_ins,
                        descuento_porcentaje=Decimal(str(desc_pct)) if desc_pct > 0 else None,
                        descuento_fijo=Decimal(str(desc_fijo)) if desc_fijo > 0 else None,
                        activa=True,
                        provisional=False,
                    )
                    s.add(ins)
                    s.flush()
                    cuotas = generate_cuotas(s, ins, generar_matricula=gen_mat)
                    log_action(s, "INSCRIPCION", "inscripcion", ins.id, {
                        "alumno_id": al_id, "curso_id": cid_sel,
                        "cuotas_generadas": len(cuotas),
                    })
                st.session_state.al_msg = f"Inscripción creada. {len(cuotas)} cuotas generadas."
                st.rerun()
        else:
            st.warning("No hay cursos activos. Cargá cursos primero.")

# ── Tab: Estado de cuenta ──────────────────────────────────────────────────

with tab_cuenta:
    if not al_id or not detail:
        st.info("Seleccioná un alumno.")
    else:
        st.subheader(f"{detail['apellido']}, {detail['nombre']}")
        cuotas_ec = detail["cuotas_ec"]

        if cuotas_ec:
            impagas = sum(1 for c in cuotas_ec if c["_estado_raw"] in ("pendiente", "parcial"))
            deuda = sum(c["_saldo_float"] for c in cuotas_ec if c["_estado_raw"] in ("pendiente", "parcial"))
            m1, m2 = st.columns(2)
            m1.metric("Cuotas impagas", impagas)
            m2.metric("Total adeudado", fmt_moneda(deuda))

            _display_keys = ["Curso", "Tipo", "Período", "Vencimiento", "Monto", "Saldo", "Estado", "Días atraso"]
            st.dataframe(
                pd.DataFrame([{k: c[k] for k in _display_keys} for c in cuotas_ec]),
                use_container_width=True, hide_index=True,
            )

            # ── Pagos recibidos ────────────────────────────────────────────
            pagos_ec = detail.get("pagos_ec", [])
            if pagos_ec:
                st.divider()
                st.subheader("Pagos recibidos")
                total_cobrado = sum(p["_monto_float"] for p in pagos_ec)
                st.dataframe(
                    pd.DataFrame([{k: v for k, v in p.items() if not k.startswith("_")} for p in pagos_ec]),
                    use_container_width=True, hide_index=True,
                )
                pc1, pc2, pc3 = st.columns(3)
                pc1.metric("Total cobrado", fmt_moneda(total_cobrado))
                pc2.metric("Total adeudado", fmt_moneda(deuda))
                pc3.metric("Saldo pendiente", fmt_moneda(deuda))

            # ── Cambio masivo de monto ─────────────────────────────────────
            with st.expander("💰 Cambiar monto a todas las cuotas impagas"):
                with st.form("bulk_monto_form"):
                    _bulk_monto = st.number_input(
                        "Nuevo monto $", min_value=0.0, step=100.0, key="bulk_monto_input"
                    )
                    _bulk_submit = st.form_submit_button("Aplicar a todas las pendientes/parciales")
                if _bulk_submit and _bulk_monto > 0:
                    with get_session() as s:
                        _n_bulk = actualizar_monto_cuotas_alumno(s, al_id, Decimal(str(_bulk_monto)))
                        log_action(s, "BULK_MONTO_CUOTAS", "alumno", al_id,
                                   {"nuevo_monto": _bulk_monto, "cuotas": _n_bulk})
                    st.session_state.al_msg = f"Monto actualizado en {_n_bulk} cuotas."
                    st.rerun()

            # ── Editar / eliminar cuota individual ─────────────────────────
            _editables = [c for c in cuotas_ec if c["_estado_raw"] in ("pendiente", "parcial")]
            if _editables:
                with st.expander("✏️ Editar o eliminar cuota individual"):
                    _c_labels = [
                        f"{c['Tipo']} {c['Período']} — {c['Monto']} (vto {c['Vencimiento']})"
                        for c in _editables
                    ]
                    _c_ids = [c["_id"] for c in _editables]
                    _sel_label = st.selectbox("Cuota", _c_labels, key="sel_cuota_edit")
                    _sel_idx = _c_labels.index(_sel_label)
                    _sel_c = _editables[_sel_idx]
                    _sel_cid = _c_ids[_sel_idx]

                    _ec1, _ec2 = st.columns(2)
                    with _ec1:
                        with st.form("edit_cuota_form"):
                            _nuevo_monto_c = st.number_input(
                                "Nuevo monto $", min_value=0.0, step=100.0,
                                value=_sel_c["_monto_float"], key=f"nm_c_{_sel_cid}",
                            )
                            if st.form_submit_button("💾 Guardar monto"):
                                if _nuevo_monto_c > 0:
                                    with get_session() as s:
                                        actualizar_monto_cuota(s, _sel_cid, Decimal(str(_nuevo_monto_c)))
                                        log_action(s, "EDIT_CUOTA_MONTO", "cuota", _sel_cid,
                                                   {"nuevo_monto": _nuevo_monto_c})
                                    st.session_state.al_msg = "Monto de cuota actualizado."
                                    st.rerun()
                    with _ec2:
                        st.write("")
                        if not _sel_c["_tiene_imp"]:
                            if st.button("🗑️ Eliminar cuota", key=f"del_cuota_{_sel_cid}", type="secondary"):
                                with get_session() as s:
                                    _ok = eliminar_cuota(s, _sel_cid)
                                    if _ok:
                                        log_action(s, "DELETE_CUOTA", "cuota", _sel_cid, {})
                                st.session_state.al_msg = "Cuota eliminada." if _ok else "No se pudo eliminar."
                                st.rerun()
                        else:
                            st.caption("⚠️ Tiene pagos aplicados — revertí el cobro para poder eliminar.")
        else:
            st.info("Sin cuotas registradas.")

# ── Tab: Baja / Suspensión ─────────────────────────────────────────────────

with tab_baja:
    if not al_id or not detail:
        st.info("Seleccioná un alumno.")
    else:
        st.warning(
            f"**{detail['apellido']}, {detail['nombre']}** — Estado actual: `{detail['estado']}`"
        )
        with st.form("baja_form"):
            nuevo_estado = st.selectbox("Nuevo estado:", ["suspendido", "baja"])
            motivo = st.text_input("Motivo *")
            condonar_al_dar_baja = st.checkbox(
                "Condonar cuotas futuras en todas las inscripciones activas",
                value=True,
                help="Solo aplica si el nuevo estado es 'baja'",
            )
            submit_baja = st.form_submit_button("Aplicar")

        if submit_baja:
            if not motivo.strip():
                st.error("Motivo requerido.")
            else:
                with get_session() as s:
                    a = s.get(Alumno, al_id)
                    a.estado = nuevo_estado
                    a.motivo_estado = motivo.strip()
                    a.fecha_estado = date.today()
                    total_cond = 0
                    if nuevo_estado == "baja" and condonar_al_dar_baja:
                        ins_activas = s.query(Inscripcion).filter(
                            Inscripcion.alumno_id == al_id,
                            Inscripcion.activa == True,
                        ).all()
                        for ins_obj in ins_activas:
                            total_cond += dar_de_baja_inscripcion(s, ins_obj.id, condonar_futuras=True)
                    log_action(s, "ESTADO_CHANGE", "alumno", al_id,
                               {"nuevo_estado": nuevo_estado, "motivo": motivo, "cuotas_condonadas": total_cond})
                msg = f"Estado cambiado a '{nuevo_estado}'."
                if total_cond:
                    msg += f" {total_cond} cuotas condonadas."
                st.session_state.al_msg = msg
                st.rerun()

# ── Tab: Matrícula Preferencial ────────────────────────────────────────────

with tab_provisional:
    st.subheader("⭐ Matrícula preferencial — reserva de cupo")
    st.info(
        "Reserva el cupo para el año siguiente. Genera una cuota de matrícula preferencial. "
        "Las cuotas mensuales se generan al confirmar la inscripción."
    )

    if not al_id or not detail:
        st.warning("Seleccioná un alumno primero.")
    else:
        st.markdown(f"**Alumno:** {detail['apellido']}, {detail['nombre']}")

        cursos_activos = load_cursos_activos()
        if not cursos_activos:
            st.warning("No hay cursos activos.")
        else:
            with st.form("prov_form"):
                c_labels = [c["label"] for c in cursos_activos]
                c_ids = [c["id"] for c in cursos_activos]
                c_sel_p = st.selectbox("Curso para el año siguiente *", c_labels)

                pc1, pc2, pc3 = st.columns(3)
                anio_res = pc1.number_input(
                    "Año que se reserva", min_value=2025, max_value=2040,
                    value=date.today().year + 1, step=1
                )
                monto_res = pc2.number_input(
                    "Monto matrícula preferencial $", min_value=0.0, step=500.0
                )
                fecha_vto_res = pc3.date_input(
                    "Vencimiento", value=date(date.today().year, 12, 31), format="DD/MM/YYYY"
                )
                submit_prov = st.form_submit_button("Crear reserva de cupo")

            if submit_prov:
                if monto_res <= 0:
                    st.error("El monto debe ser mayor a 0.")
                else:
                    cid_p = c_ids[c_labels.index(c_sel_p)]
                    with get_session() as s:
                        ins_p, cuota_p = generate_inscripcion_provisional(
                            s, al_id, cid_p,
                            Decimal(str(monto_res)),
                            fecha_vto_res,
                            int(anio_res),
                        )
                        log_action(s, "INSCRIPCION_PROVISIONAL", "inscripcion", ins_p.id, {
                            "alumno_id": al_id, "curso_id": cid_p,
                            "monto_reserva": str(monto_res), "anio": anio_res,
                        })
                    st.session_state.al_msg = (
                        f"Reserva creada para {int(anio_res)}. "
                        f"Cuota de {fmt_moneda(monto_res)} generada."
                    )
                    st.rerun()

        # Mostrar reservas existentes de este alumno
        if detail:
            provisionales = [i for i in detail["inscripciones"] if i["provisional"]]
            if provisionales:
                st.divider()
                st.markdown("**Reservas existentes de este alumno**")
                for p in provisionales:
                    st.markdown(
                        f"- {p['curso']} — Año {p['anio_reserva'] or '?'} — "
                        f"Deuda: {fmt_moneda(p['deuda_total'])} — "
                        f"{'Activa' if p['activa'] else 'Inactiva'}"
                    )

# ── Tab: Importar Excel ────────────────────────────────────────────────────

with tab_import:
    st.subheader("📥 Importar alumnos desde Excel")

    if st.session_state.get("al_import_errores"):
        with st.expander(f"⚠️ {len(st.session_state.al_import_errores)} errores de la última importación", expanded=True):
            for e in st.session_state.al_import_errores:
                st.write(f"• {e}")
        if st.button("Limpiar errores", key="clear_import_err"):
            del st.session_state["al_import_errores"]
            st.rerun()

    template_df = pd.DataFrame({
        "apellido": ["García"],
        "nombre": ["María"],
        "dni": ["30123456"],
        "sede": ["Sede Centro"],
        "telefono": ["1155554444"],
        "email": ["maria@mail.com"],
        "fecha_nacimiento": ["15/03/1995"],
        "referente_nombre": ["García, Carlos"],
        "referente_cuit": ["20301234567"],
        "referente_vinculo": ["padre"],
        "referente_telefono": ["1155551111"],
        "nombre_curso": ["Inglés Básico"],
        "cantidad_cuotas": [6],
        "fecha_inscripcion": ["01/03/2026"],
    })
    buf = BytesIO()
    template_df.to_excel(buf, index=False)
    buf.seek(0)
    st.download_button(
        "⬇️ Descargar plantilla",
        data=buf,
        file_name="plantilla_alumnos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown(
        "**Columnas requeridas:** `apellido`, `nombre`, `dni`, `sede`  \n"
        "**Opcionales:** `telefono`, `email`, `fecha_nacimiento` (DD/MM/AAAA), "
        "`referente_nombre`, `referente_cuit`, `referente_vinculo`, `referente_telefono`  \n"
        "**Con inscripción:** `nombre_curso` (nombre exacto del curso), "
        "`cantidad_cuotas` (default: usa el del curso), "
        "`fecha_inscripcion` (DD/MM/AAAA, default: hoy)"
    )

    uploaded = st.file_uploader("Subir archivo", type=["xlsx", "xls"])
    if uploaded:
        try:
            df_imp = pd.read_excel(uploaded)
            df_imp.columns = [str(c).lower().strip() for c in df_imp.columns]
            required = {"apellido", "nombre", "dni", "sede"}
            missing = required - set(df_imp.columns)
            if missing:
                st.error(f"Faltan columnas: {missing}")
            else:
                st.write(f"**{len(df_imp)} filas detectadas. Primeras 5:**")
                st.dataframe(df_imp.head(5), use_container_width=True)

                solo_inscripciones = st.checkbox(
                    "Solo agregar inscripciones (alumnos ya existen en el sistema)",
                    value=False,
                    help="Marca esta opción si los alumnos ya fueron importados y solo querés cargarles los cursos.",
                )

                if st.button("✅ Confirmar importación"):
                    from datetime import datetime as _dt
                    with get_session() as s:
                        all_sedes = {r.nombre.lower(): r.id for r in s.query(Sede).all()}
                        all_cursos = {
                            r.nombre.lower(): r for r in s.query(Curso).filter(Curso.activo == True).all()
                        }
                        created, inscripciones_add, errores, cuotas_gen = 0, 0, [], 0

                        for _, row in df_imp.iterrows():
                            dni_val = str(row.get("dni", "")).strip().split(".")[0]
                            if not dni_val:
                                errores.append("Fila sin DNI, omitida")
                                continue

                            alumno_existente = s.query(Alumno).filter(Alumno.dni == dni_val).first()

                            if solo_inscripciones:
                                if not alumno_existente:
                                    errores.append(f"DNI {dni_val}: no existe en el sistema, omitido")
                                    continue
                                al = alumno_existente
                            else:
                                sede_k = str(row.get("sede", "")).strip().lower()
                                sede_id_row = all_sedes.get(sede_k)
                                if not sede_id_row:
                                    errores.append(f"DNI {dni_val}: sede '{row.get('sede')}' no encontrada")
                                    continue
                                if alumno_existente:
                                    errores.append(f"DNI {dni_val}: ya existe, omitido (usá el modo 'Solo inscripciones')")
                                    continue

                                fn = None
                                fn_raw = row.get("fecha_nacimiento", "")
                                if pd.notna(fn_raw) and str(fn_raw).strip():
                                    try:
                                        if hasattr(fn_raw, "date"):
                                            fn = fn_raw.date()
                                        else:
                                            fn = _dt.strptime(str(fn_raw).strip()[:10], "%d/%m/%Y").date()
                                    except Exception:
                                        pass

                                al = Alumno(
                                    apellido=str(row.get("apellido", "")).strip(),
                                    nombre=str(row.get("nombre", "")).strip(),
                                    dni=dni_val,
                                    sede_id=sede_id_row,
                                    telefono=str(row.get("telefono", "")).strip() or None,
                                    email=str(row.get("email", "")).strip() or None,
                                    fecha_nacimiento=fn,
                                    estado=EstadoAlumnoEnum.activo,
                                )
                                s.add(al)
                                s.flush()

                                rn = str(row.get("referente_nombre", "")).strip()
                                rc = str(row.get("referente_cuit", "")).strip()
                                if rn and rc:
                                    rv = str(row.get("referente_vinculo", "mismo_alumno")).strip().lower()
                                    valid_v = ["padre", "madre", "empresa", "otro", "mismo_alumno"]
                                    s.add(ReferentePago(
                                        alumno_id=al.id,
                                        nombre_completo=rn,
                                        cuit_cuil=rc,
                                        vinculo=rv if rv in valid_v else "mismo_alumno",
                                        telefono=str(row.get("referente_telefono", "")).strip() or None,
                                        es_default=True,
                                    ))

                                log_action(s, "IMPORT", "alumno", al.id, {"dni": dni_val})
                                created += 1

                            # Inscripción (aplica en ambos modos)
                            curso_k = str(row.get("nombre_curso", "")).strip().lower()
                            if curso_k:
                                curso_obj = all_cursos.get(curso_k)
                                if not curso_obj:
                                    cursos_disponibles = ", ".join(sorted(all_cursos.keys()))
                                    errores.append(f"DNI {dni_val}: curso '{row.get('nombre_curso')}' no encontrado. Disponibles: {cursos_disponibles}")
                                else:
                                    ya_inscripto = s.query(Inscripcion).filter(
                                        Inscripcion.alumno_id == al.id,
                                        Inscripcion.curso_id == curso_obj.id,
                                        Inscripcion.activa == True,
                                    ).first()
                                    if ya_inscripto:
                                        errores.append(f"DNI {dni_val}: ya inscripto en '{curso_obj.nombre}', omitido")
                                    else:
                                        fi_raw = row.get("fecha_inscripcion", "")
                                        fi = date.today()
                                        if pd.notna(fi_raw) and str(fi_raw).strip():
                                            try:
                                                if hasattr(fi_raw, "date"):
                                                    fi = fi_raw.date()
                                                else:
                                                    fi = _dt.strptime(str(fi_raw).strip()[:10], "%d/%m/%Y").date()
                                            except Exception:
                                                pass
                                        cant_raw = row.get("cantidad_cuotas", "")
                                        cant_ov = None
                                        if pd.notna(cant_raw) and str(cant_raw).strip():
                                            try:
                                                cant_ov = int(float(str(cant_raw)))
                                            except Exception:
                                                pass

                                        ins = Inscripcion(
                                            alumno_id=al.id,
                                            curso_id=curso_obj.id,
                                            fecha_inscripcion=fi,
                                            activa=True,
                                            provisional=False,
                                        )
                                        s.add(ins)
                                        s.flush()
                                        cuotas = generate_cuotas(s, ins, cantidad_override=cant_ov)
                                        cuotas_gen += len(cuotas)
                                        inscripciones_add += 1
                                        log_action(s, "IMPORT_INSCRIPCION", "inscripcion", ins.id, {
                                            "alumno_id": al.id, "curso_id": curso_obj.id,
                                            "cuotas": len(cuotas),
                                        })

                    msg_parts = []
                    if created:
                        msg_parts.append(f"{created} alumnos importados")
                    if inscripciones_add:
                        msg_parts.append(f"{inscripciones_add} inscripciones agregadas")
                    if cuotas_gen:
                        msg_parts.append(f"{cuotas_gen} cuotas generadas")
                    st.session_state.al_msg = ", ".join(msg_parts) + "." if msg_parts else "Sin cambios."
                    st.session_state.al_import_errores = errores if errores else []
                    st.rerun()

        except Exception as e:
            st.error(f"Error leyendo el archivo: {e}")
