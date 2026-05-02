from decimal import Decimal

import pandas as pd
import streamlit as st

from db.models import Curso, Sede
from db.session import get_session
from services.enrollment import (
    generate_exam_cuotas,
    recalculate_pending_cuotas,
    save_price_history,
)
from utils.audit import log_action
from utils.formatters import fmt_moneda

st.set_page_config(page_title="Cursos", page_icon="📚", layout="wide")
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

    # ── Derecho de examen ──────────────────────────────────────────────────
    st.divider()
    st.subheader("📝 Cargar derecho de examen")
    todos_cursos = load_cursos()
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
