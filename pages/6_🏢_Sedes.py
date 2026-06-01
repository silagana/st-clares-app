import pandas as pd
import streamlit as st

from db.models import Sede
from db.session import get_session
from utils.audit import log_action
from utils.auth import require_login

st.set_page_config(page_title="Sedes", page_icon="🏢", layout="wide")
require_login()
st.title("🏢 Sedes")

for k, v in [("sede_edit_id", None), ("sede_msg", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.sede_msg:
    st.success(st.session_state.sede_msg)
    st.session_state.sede_msg = None


def load_sedes():
    with get_session() as s:
        rows = s.query(Sede).order_by(Sede.nombre).all()
        return [
            {
                "id": r.id,
                "Nombre": r.nombre,
                "Encargada": r.encargada_nombre or "",
                "Teléfono": r.encargada_telefono or "",
                "Día vto.": r.dia_vencimiento_default,
                "Activa": "✅" if r.activa else "❌",
            }
            for r in rows
        ]


def load_sede(sede_id):
    with get_session() as s:
        r = s.get(Sede, sede_id)
        if not r:
            return None
        return {
            "nombre": r.nombre,
            "encargada_nombre": r.encargada_nombre or "",
            "encargada_telefono": r.encargada_telefono or "",
            "dia_vencimiento_default": r.dia_vencimiento_default,
            "activa": r.activa,
        }


col_table, col_form = st.columns([3, 2])

with col_table:
    sedes = load_sedes()
    if not sedes:
        st.info("No hay sedes cargadas.")
    else:
        df = pd.DataFrame(sedes).drop(columns=["id"])
        st.dataframe(df, use_container_width=True, hide_index=True)

    names = [s["Nombre"] for s in sedes]
    ids = [s["id"] for s in sedes]
    sel = st.selectbox("Seleccionar para editar:", ["— nueva sede —"] + names)
    if sel != "— nueva sede —":
        st.session_state.sede_edit_id = ids[names.index(sel)]
    else:
        st.session_state.sede_edit_id = None

with col_form:
    edit_id = st.session_state.sede_edit_id
    is_new = edit_id is None
    d = ({} if is_new else load_sede(edit_id)) or {}

    st.subheader("➕ Nueva sede" if is_new else "✏️ Editar sede")

    with st.form("sede_form"):
        nombre = st.text_input("Nombre *", value=d.get("nombre", ""))
        enc_nombre = st.text_input("Nombre encargada", value=d.get("encargada_nombre", ""))
        enc_tel = st.text_input("Teléfono encargada", value=d.get("encargada_telefono", ""))
        dia_vto = st.number_input(
            "Día de vencimiento (1–28)",
            min_value=1, max_value=28,
            value=d.get("dia_vencimiento_default", 10),
        )
        activa = st.checkbox("Activa", value=d.get("activa", True))
        submit = st.form_submit_button("Guardar")

    if submit:
        if not nombre.strip():
            st.error("El nombre es obligatorio.")
        else:
            with get_session() as s:
                if is_new:
                    obj = Sede(
                        nombre=nombre.strip(),
                        encargada_nombre=enc_nombre.strip() or None,
                        encargada_telefono=enc_tel.strip() or None,
                        dia_vencimiento_default=int(dia_vto),
                        activa=activa,
                    )
                    s.add(obj)
                    s.flush()
                    log_action(s, "CREATE", "sede", obj.id, {"nombre": nombre})
                    st.session_state.sede_msg = f"Sede '{nombre}' creada."
                else:
                    obj = s.get(Sede, edit_id)
                    obj.nombre = nombre.strip()
                    obj.encargada_nombre = enc_nombre.strip() or None
                    obj.encargada_telefono = enc_tel.strip() or None
                    obj.dia_vencimiento_default = int(dia_vto)
                    obj.activa = activa
                    log_action(s, "UPDATE", "sede", edit_id, {"nombre": nombre})
                    st.session_state.sede_msg = f"Sede '{nombre}' actualizada."
            st.rerun()
