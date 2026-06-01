from datetime import date
from decimal import Decimal
from io import BytesIO

import pandas as pd
import streamlit as st

from db.models import Curso, Inscripcion, ModalidadEnum, Sede
from db.session import get_session
from services.enrollment import (
    generate_cuotas_masivas,
    generate_exam_cuotas,
    recalculate_pending_cuotas,
    save_price_history,
)
from utils.audit import log_action
from utils.auth import require_login
from utils.formatters import fmt_moneda

st.set_page_config(page_title="Cursos", page_icon="📚", layout="wide")
require_login()
st.title("📚 Cursos")

for k, v in [("curso_edit_id", None), ("curso_precio_confirm", None), ("curso_msg", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.curso_msg:
    st.success(st.session_state.curso_msg)
    st.session_state.curso_msg = None


# ── Loaders ────────────────────────────────────────────────────────────────

def load_sedes_map():
    with get_session() as s:
        return {r.nombre: r.id for r in s.query(Sede).filter(Sede.activa == True).order_by(Sede.nombre)}


def load_cursos(sede_id=None):
    with get_session() as s:
        q = s.query(Curso)
        if sede_id:
            q = q.filter(Curso.sede_id == sede_id)
        rows = q.order_by(Curso.nombre).all()
        return [
            {
                "id": r.id,
                "Nombre": r.nombre,
                "Nivel": r.nivel or "",
                "Modalidad": r.modalidad.value,
                "Matrícula": fmt_moneda(r.monto_matricula),
                "Cuota mensual": fmt_moneda(r.monto_cuota_mensual),
                "N° cuotas": r.cantidad_cuotas,
                "Activo": "✅" if r.activo else "❌",
            }
            for r in rows
        ]


def load_curso(curso_id):
    with get_session() as s:
        r = s.get(Curso, curso_id)
        if not r:
            return None
        return {
            "nombre": r.nombre,
            "nivel": r.nivel or "",
            "modalidad": r.modalidad.value,
            "monto_matricula": float(r.monto_matricula),
            "monto_cuota_mensual": float(r.monto_cuota_mensual),
            "cantidad_cuotas": r.cantidad_cuotas,
            "activo": r.activo,
            "sede_id": r.sede_id,
        }


# ── Filtro sede ────────────────────────────────────────────────────────────

sedes_map = load_sedes_map()

col_f, _ = st.columns([2, 3])
with col_f:
    sede_filter = st.selectbox("Filtrar por sede:", ["Todas"] + list(sedes_map.keys()))
sede_id_f = sedes_map.get(sede_filter) if sede_filter != "Todas" else None

# ── Layout principal ───────────────────────────────────────────────────────

col_table, col_form = st.columns([3, 2])

with col_table:
    cursos = load_cursos(sede_id_f)
    if not cursos:
        st.info("No hay cursos para este filtro.")
    else:
        df = pd.DataFrame(cursos).drop(columns=["id"])
        st.dataframe(df, use_container_width=True, hide_index=True)

    names = [c["Nombre"] for c in cursos]
    ids = [c["id"] for c in cursos]
    sel = st.selectbox("Seleccionar para editar:", ["— nuevo curso —"] + names)
    if sel != "— nuevo curso —":
        st.session_state.curso_edit_id = ids[names.index(sel)]
    else:
        st.session_state.curso_edit_id = None

    # ── Confirmación de recálculo de precios ───────────────────────────────
    if st.session_state.curso_precio_confirm:
        cid = st.session_state.curso_precio_confirm
        st.warning("⚠️ El precio cambió. ¿Actualizar cuotas pendientes no vencidas?")
        ca, cb = st.columns(2)
        if ca.button("✅ Sí, actualizar cuotas"):
            with get_session() as s:
                count = recalculate_pending_cuotas(s, cid)
                log_action(s, "PRECIO_RECALC", "curso", cid, {"cuotas_actualizadas": count})
            st.session_state.curso_precio_confirm = None
            st.session_state.curso_msg = f"Actualizadas {count} cuotas pendientes."
            st.rerun()
        if cb.button("❌ No, dejar precio anterior en cuotas"):
            st.session_state.curso_precio_confirm = None
            st.rerun()

    # ── Generar cuotas masivas ─────────────────────────────────────────────
    st.divider()
    st.subheader("📅 Generar cuotas masivas")
    todos_cursos = load_cursos()
    if todos_cursos:
        with st.form("masiva_form"):
            m_names = [c["Nombre"] for c in todos_cursos]
            m_ids = [c["id"] for c in todos_cursos]
            m_sel = st.selectbox("Curso:", m_names, key="mas_curso")
            mc1, mc2 = st.columns(2)
            m_inicio = mc1.date_input("Mes de inicio", value=date.today().replace(day=1), format="DD/MM/YYYY")
            m_cant = mc2.number_input("Cantidad de meses", min_value=1, max_value=24, value=6)
            submit_mas = st.form_submit_button("Vista previa")

        if submit_mas:
            cid_mas = m_ids[m_names.index(m_sel)]
            with get_session() as s:
                n_ins = s.query(Inscripcion).filter(
                    Inscripcion.curso_id == cid_mas, Inscripcion.activa == True
                ).count()
            st.session_state.masiva_preview = {
                "curso_id": cid_mas,
                "curso_nombre": m_sel,
                "inicio": m_inicio,
                "cantidad": int(m_cant),
                "n_inscriptos": n_ins,
            }

        if "masiva_preview" in st.session_state:
            p = st.session_state.masiva_preview
            st.info(
                f"Se generarán hasta **{p['cantidad']} cuotas** para "
                f"**{p['n_inscriptos']} inscriptos** de **{p['curso_nombre']}**, "
                f"desde **{p['inicio'].strftime('%m/%Y')}**. "
                f"Períodos ya existentes serán saltados."
            )
            if st.button("✅ Confirmar generación masiva", type="primary", key="btn_masiva_go"):
                with get_session() as s:
                    gen, afect = generate_cuotas_masivas(s, p["curso_id"], p["inicio"], p["cantidad"])
                    log_action(s, "CUOTAS_MASIVAS", "curso", p["curso_id"], {
                        "cuotas": gen, "inscripciones": afect,
                        "inicio": str(p["inicio"]), "cantidad": p["cantidad"],
                    })
                del st.session_state.masiva_preview
                st.session_state.curso_msg = f"Generadas {gen} cuotas en {afect} inscripciones."
                st.rerun()

    # ── Derecho de examen ──────────────────────────────────────────────────
    st.divider()
    st.subheader("📝 Cargar derecho de examen")
    if todos_cursos:
        with st.form("examen_form"):
            ex_names = [c["Nombre"] for c in todos_cursos]
            ex_ids = [c["id"] for c in todos_cursos]
            ex_sel = st.selectbox("Curso:", ex_names)
            ex_monto = st.number_input("Monto $", min_value=0.0, step=100.0)
            submit_ex = st.form_submit_button("Generar cuotas de examen")
        if submit_ex and ex_sel:
            cid_ex = ex_ids[ex_names.index(ex_sel)]
            with get_session() as s:
                count = generate_exam_cuotas(s, cid_ex, Decimal(str(ex_monto)))
                log_action(s, "EXAMEN_CUOTAS", "curso", cid_ex, {"monto": str(ex_monto), "count": count})
            st.session_state.curso_msg = f"Generadas {count} cuotas de examen."
            st.rerun()

with col_form:
    edit_id = st.session_state.curso_edit_id
    is_new = edit_id is None
    d = ({} if is_new else load_curso(edit_id)) or {}

    st.subheader("➕ Nuevo curso" if is_new else "✏️ Editar curso")

    sede_names = list(sedes_map.keys())
    sede_ids = list(sedes_map.values())
    cur_sede_idx = sede_ids.index(d["sede_id"]) if d.get("sede_id") in sede_ids else 0

    with st.form("curso_form"):
        sede_sel = st.selectbox("Sede *", sede_names, index=cur_sede_idx)
        nombre = st.text_input("Nombre *", value=d.get("nombre", ""))
        nivel = st.text_input("Nivel", value=d.get("nivel", ""))
        modalidad = st.selectbox(
            "Modalidad", ["presencial", "online"],
            index=0 if d.get("modalidad", "presencial") == "presencial" else 1,
        )
        col_a, col_b = st.columns(2)
        monto_mat = col_a.number_input("Matrícula $", min_value=0.0, step=100.0,
                                        value=d.get("monto_matricula", 0.0))
        monto_cuota = col_b.number_input("Cuota mensual $", min_value=0.0, step=100.0,
                                          value=d.get("monto_cuota_mensual", 0.0))
        cant_cuotas = st.number_input("Cantidad de cuotas", min_value=1, max_value=24,
                                       value=d.get("cantidad_cuotas", 10))
        activo = st.checkbox("Activo", value=d.get("activo", True))
        submit = st.form_submit_button("Guardar")

    if submit:
        if not nombre.strip():
            st.error("El nombre es obligatorio.")
        elif not sede_names:
            st.error("Primero cargá al menos una sede.")
        else:
            sede_id_sel = sedes_map[sede_sel]
            with get_session() as s:
                if is_new:
                    obj = Curso(
                        sede_id=sede_id_sel,
                        nombre=nombre.strip(),
                        nivel=nivel.strip() or None,
                        modalidad=modalidad,
                        monto_matricula=Decimal(str(monto_mat)),
                        monto_cuota_mensual=Decimal(str(monto_cuota)),
                        cantidad_cuotas=int(cant_cuotas),
                        activo=activo,
                    )
                    s.add(obj)
                    s.flush()
                    log_action(s, "CREATE", "curso", obj.id, {"nombre": nombre})
                    st.session_state.curso_msg = f"Curso '{nombre}' creado."
                else:
                    obj = s.get(Curso, edit_id)
                    precio_cambio = (
                        Decimal(str(monto_cuota)) != obj.monto_cuota_mensual
                        or Decimal(str(monto_mat)) != obj.monto_matricula
                    )
                    if precio_cambio:
                        save_price_history(s, obj, "Actualización manual")
                    obj.sede_id = sede_id_sel
                    obj.nombre = nombre.strip()
                    obj.nivel = nivel.strip() or None
                    obj.modalidad = modalidad
                    obj.monto_matricula = Decimal(str(monto_mat))
                    obj.monto_cuota_mensual = Decimal(str(monto_cuota))
                    obj.cantidad_cuotas = int(cant_cuotas)
                    obj.activo = activo
                    log_action(s, "UPDATE", "curso", edit_id, {"nombre": nombre, "precio_cambio": precio_cambio})
                    if precio_cambio:
                        st.session_state.curso_precio_confirm = edit_id
                    st.session_state.curso_msg = f"Curso '{nombre}' actualizado."
            st.rerun()


# ── Excel export / import ──────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Exportar / Importar cursos")

exp_col, imp_col = st.columns(2)

with exp_col:
    st.caption("Exportar todos los cursos como plantilla editable.")
    with get_session() as s:
        all_c = s.query(Curso).join(Sede, Curso.sede_id == Sede.id).order_by(Sede.nombre, Curso.nombre).all()
        export_data = [
            {
                "sede": c.sede.nombre,
                "nombre": c.nombre,
                "nivel": c.nivel or "",
                "modalidad": c.modalidad.value,
                "monto_matricula": float(c.monto_matricula),
                "monto_cuota_mensual": float(c.monto_cuota_mensual),
                "cantidad_cuotas": c.cantidad_cuotas,
                "activo": c.activo,
            }
            for c in all_c
        ]
    if not export_data:
        # Empty template when no courses exist
        export_data = [{
            "sede": "Sede Centro",
            "nombre": "Inglés Básico",
            "nivel": "A1",
            "modalidad": "presencial",
            "monto_matricula": 5000.0,
            "monto_cuota_mensual": 10000.0,
            "cantidad_cuotas": 10,
            "activo": True,
        }]
    buf_exp = BytesIO()
    pd.DataFrame(export_data).to_excel(buf_exp, index=False)
    buf_exp.seek(0)
    st.download_button(
        "⬇️ Exportar / plantilla",
        data=buf_exp,
        file_name="cursos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

with imp_col:
    st.caption(
        "Importar desde Excel. Columnas requeridas: `sede`, `nombre`, `modalidad`, "
        "`monto_matricula`, `monto_cuota_mensual`, `cantidad_cuotas`. "
        "Si el curso (nombre+sede) ya existe, se actualiza."
    )
    uploaded_c = st.file_uploader("Subir cursos.xlsx", type=["xlsx", "xls"], key="cursos_upload")
    if uploaded_c:
        try:
            df_c = pd.read_excel(uploaded_c)
            df_c.columns = [str(c).lower().strip() for c in df_c.columns]
            req_c = {"sede", "nombre", "modalidad", "monto_matricula", "monto_cuota_mensual", "cantidad_cuotas"}
            missing_c = req_c - set(df_c.columns)
            if missing_c:
                st.error(f"Faltan columnas: {missing_c}")
            else:
                st.write(f"**{len(df_c)} filas. Primeras 5:**")
                st.dataframe(df_c.head(5), use_container_width=True)

                if st.button("✅ Confirmar importación cursos", key="btn_imp_cursos"):
                    with get_session() as s:
                        all_sedes_map = {r.nombre.lower(): r.id for r in s.query(Sede).all()}
                        created_c = updated_c = 0
                        errores_c = []

                        for _, row in df_c.iterrows():
                            sede_raw = row.get("sede", "")
                            sede_k = "" if pd.isna(sede_raw) else str(sede_raw).strip().lower()
                            nombre_raw = row.get("nombre", "")
                            nombre_c = "" if pd.isna(nombre_raw) else str(nombre_raw).strip()
                            if not nombre_c:
                                errores_c.append("Fila sin nombre, omitida")
                                continue
                            sid = all_sedes_map.get(sede_k)
                            if not sid:
                                errores_c.append(f"'{nombre_c}': sede '{row.get('sede')}' no encontrada")
                                continue
                            modalidad_v = str(row.get("modalidad", "presencial")).strip().lower()
                            if modalidad_v not in ("presencial", "online"):
                                modalidad_v = "presencial"
                            try:
                                def _safe_decimal(val, default=0):
                                    if val is None or (isinstance(val, float) and __import__('math').isnan(val)):
                                        return Decimal(str(default))
                                    return Decimal(str(val))

                                def _safe_int(val, default=10):
                                    if val is None or (isinstance(val, float) and __import__('math').isnan(val)):
                                        return default
                                    return int(float(str(val)))

                                mat = _safe_decimal(row.get("monto_matricula"), 0)
                                cuota = _safe_decimal(row.get("monto_cuota_mensual"), 0)
                                cant = _safe_int(row.get("cantidad_cuotas"), 10)
                                activo_raw = row.get("activo", True)
                                activo_v = False if str(activo_raw).strip().lower() in ("false", "0", "no") else True
                            except Exception:
                                errores_c.append(f"'{nombre_c}': valores numéricos inválidos")
                                continue

                            existing_c = s.query(Curso).filter(
                                Curso.nombre == nombre_c, Curso.sede_id == sid
                            ).first()
                            if existing_c:
                                existing_c.modalidad = modalidad_v
                                existing_c.monto_matricula = mat
                                existing_c.monto_cuota_mensual = cuota
                                existing_c.cantidad_cuotas = cant
                                existing_c.activo = activo_v
                                existing_c.nivel = str(row.get("nivel", "")).strip() or None
                                log_action(s, "UPDATE", "curso", existing_c.id, {"nombre": nombre_c})
                                updated_c += 1
                            else:
                                new_c = Curso(
                                    sede_id=sid,
                                    nombre=nombre_c,
                                    nivel=str(row.get("nivel", "")).strip() or None,
                                    modalidad=modalidad_v,
                                    monto_matricula=mat,
                                    monto_cuota_mensual=cuota,
                                    cantidad_cuotas=cant,
                                    activo=activo_v,
                                )
                                s.add(new_c)
                                s.flush()
                                log_action(s, "CREATE", "curso", new_c.id, {"nombre": nombre_c})
                                created_c += 1

                    st.session_state.curso_msg = f"Creados: {created_c}, actualizados: {updated_c}."
                    if errores_c:
                        with st.expander(f"⚠️ {len(errores_c)} errores"):
                            for e in errores_c:
                                st.write(f"• {e}")
                    st.rerun()

        except Exception as e:
            st.error(f"Error leyendo el archivo: {e}")
