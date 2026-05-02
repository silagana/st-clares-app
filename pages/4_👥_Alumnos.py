from datetime import date
from decimal import Decimal
from io import BytesIO

import pandas as pd
import streamlit as st

from db.models import (
    Alumno, Curso, EstadoAlumnoEnum, EstadoCuotaEnum,
    Inscripcion, ReferentePago, Sede,
)
from db.session import get_session
from services.enrollment import generate_cuotas
from utils.audit import log_action
from utils.formatters import fmt_fecha, fmt_moneda

st.set_page_config(page_title="Alumnos", page_icon="👥", layout="wide")
st.title("👥 Alumnos")

for k, v in [("al_id", None), ("al_msg", None)]:
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
        inscripciones = [
            {
                "id": ins.id,
                "curso": ins.curso.nombre,
                "sede": ins.curso.sede.nombre,
                "fecha": ins.fecha_inscripcion,
                "descuento_pct": float(ins.descuento_porcentaje) if ins.descuento_porcentaje else 0.0,
                "descuento_fijo": float(ins.descuento_fijo) if ins.descuento_fijo else 0.0,
                "activa": ins.activa,
                "cuotas_total": len(ins.cuotas),
                "cuotas_impagas": sum(
                    1 for c in ins.cuotas
                    if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial)
                ),
                "deuda_total": sum(
                    float(c.saldo_pendiente)
                    for c in ins.cuotas
                    if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial)
                ),
            }
            for ins in r.inscripciones
        ]
        cuotas_ec = []
        for ins in r.inscripciones:
            for c in sorted(ins.cuotas, key=lambda x: x.fecha_vencimiento):
                dias_atraso = 0
                if c.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                    dias_atraso = max(0, (date.today() - c.fecha_vencimiento).days)
                cuotas_ec.append({
                    "Curso": ins.curso.nombre,
                    "Tipo": c.tipo.value,
                    "Período": c.periodo or "-",
                    "Vencimiento": fmt_fecha(c.fecha_vencimiento),
                    "Monto": fmt_moneda(c.monto_actualizado),
                    "Saldo": fmt_moneda(c.saldo_pendiente),
                    "Estado": c.estado.value,
                    "Días atraso": dias_atraso,
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
            "inscripciones": inscripciones,
            "cuotas_ec": cuotas_ec,
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

if alumnos:
    df = pd.DataFrame(alumnos).drop(columns=["id"])
    st.dataframe(df, use_container_width=True, hide_index=True)
    names = [a["Alumno"] for a in alumnos]
    ids = [a["id"] for a in alumnos]
    sel = st.selectbox("Seleccionar alumno:", ["— seleccionar —"] + names)
    if sel != "— seleccionar —":
        st.session_state.al_id = ids[names.index(sel)]
else:
    st.info("Sin alumnos para estos filtros.")
    if st.button("➕ Nuevo alumno"):
        st.session_state.al_id = None

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────

tab_edit, tab_cuenta, tab_baja, tab_import = st.tabs([
    "✏️ Alta / Edición",
    "📊 Estado de cuenta",
    "🚫 Baja / Suspensión",
    "📥 Importar Excel",
])

al_id = st.session_state.al_id
detail = load_alumno_detail(al_id) if al_id else None

# ── Tab: Alta / Edición ────────────────────────────────────────────────────

with tab_edit:
    is_new = al_id is None
    d = detail or {}
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

            st.session_state.al_msg = "Alumno guardado."
            st.rerun()

    # Inscripciones
    if al_id and detail:
        st.divider()
        st.subheader("📚 Inscripciones")

        if detail["inscripciones"]:
            ins_rows = [{
                "Curso": i["curso"],
                "Sede": i["sede"],
                "Fecha": fmt_fecha(i["fecha"]),
                "Cuotas": i["cuotas_total"],
                "Impagas": i["cuotas_impagas"],
                "Deuda": fmt_moneda(i["deuda_total"]),
                "Activa": "✅" if i["activa"] else "❌",
            } for i in detail["inscripciones"]]
            st.dataframe(pd.DataFrame(ins_rows), use_container_width=True, hide_index=True)
        else:
            st.info("Sin inscripciones.")

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
                    )
                    s.add(ins)
                    s.flush()
                    cuotas = generate_cuotas(s, ins)
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
            impagas = sum(1 for c in cuotas_ec if c["Estado"] in ("pendiente", "parcial"))
            deuda = sum(
                float(c["Saldo"].replace("$ ", "").replace(".", "").replace(",", "."))
                for c in cuotas_ec if c["Estado"] in ("pendiente", "parcial")
            )
            m1, m2 = st.columns(2)
            m1.metric("Cuotas impagas", impagas)
            m2.metric("Total adeudado", fmt_moneda(deuda))
            st.dataframe(pd.DataFrame(cuotas_ec), use_container_width=True, hide_index=True)
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
                    log_action(s, "ESTADO_CHANGE", "alumno", al_id,
                               {"nuevo_estado": nuevo_estado, "motivo": motivo})
                st.session_state.al_msg = f"Estado cambiado a '{nuevo_estado}'."
                st.rerun()

# ── Tab: Importar Excel ────────────────────────────────────────────────────

with tab_import:
    st.subheader("📥 Importar alumnos desde Excel")

    # Template
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
        "`referente_nombre`, `referente_cuit`, `referente_vinculo`, `referente_telefono`"
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

                if st.button("✅ Confirmar importación"):
                    with get_session() as s:
                        all_sedes = {r.nombre.lower(): r.id for r in s.query(Sede).all()}
                        created, errores = 0, []

                        for _, row in df_imp.iterrows():
                            dni_val = str(row.get("dni", "")).strip().split(".")[0]
                            if not dni_val:
                                errores.append("Fila sin DNI, omitida")
                                continue
                            sede_k = str(row.get("sede", "")).strip().lower()
                            sede_id_row = all_sedes.get(sede_k)
                            if not sede_id_row:
                                errores.append(f"DNI {dni_val}: sede '{row.get('sede')}' no encontrada")
                                continue
                            if s.query(Alumno).filter(Alumno.dni == dni_val).first():
                                errores.append(f"DNI {dni_val}: ya existe")
                                continue

                            fn = None
                            fn_raw = row.get("fecha_nacimiento", "")
                            if pd.notna(fn_raw) and str(fn_raw).strip():
                                try:
                                    from datetime import datetime as _dt
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
                                rv = str(row.get("referente_vinculo", "mismo_alumno")).strip()
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

                    st.session_state.al_msg = f"Importados {created} alumnos."
                    if errores:
                        with st.expander(f"⚠️ {len(errores)} errores"):
                            for e in errores:
                                st.write(f"• {e}")
                    st.rerun()

        except Exception as e:
            st.error(f"Error leyendo el archivo: {e}")
