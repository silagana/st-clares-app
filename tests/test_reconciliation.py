from decimal import Decimal

import pytest

from db.models import (
    EstadoCuotaEnum, EstadoMovimientoEnum, PatronNoAlumno,
    ReferentePago, VinculoEnum,
)
from services.reconciliation import (
    aplicar_conciliacion,
    revertir_conciliacion,
    sugerir_conciliacion,
)
from tests.conftest import make_movimiento


# ── CUIT matching ─────────────────────────────────────────────────────────────

def test_cuit_match_alto(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Single CUIT match → confianza=alto, metodo=cuit, imputaciones populated."""
    alumno, ref = alumno_con_referente
    mov = make_movimiento(db_session, cuit=ref.cuit_cuil, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.confianza == "alto"
    assert sug.metodo == "cuit"
    assert len(sug.imputaciones) >= 1
    assert not sug.sin_match
    assert not sug.es_no_alumno


def test_cuit_match_medio_multiple_alumnos(db_session, sede, curso, alumno_con_referente, inscripcion_con_cuotas):
    """Same CUIT on two different alumnos → confianza=medio, metodo=cuit_multiple."""
    from db.models import Alumno, EstadoAlumnoEnum, Inscripcion
    from datetime import date
    from services.enrollment import generate_cuotas

    alumno1, ref1 = alumno_con_referente

    # Second alumno with same CUIT
    alumno2 = Alumno(
        nombre="Juan", apellido="Gomez", dni="99887766",
        sede_id=sede.id, estado=EstadoAlumnoEnum.activo,
    )
    db_session.add(alumno2)
    db_session.flush()
    ref2 = ReferentePago(
        alumno_id=alumno2.id,
        nombre_completo="Gomez Roberto",
        cuit_cuil=ref1.cuit_cuil,  # same CUIT
        vinculo=VinculoEnum.padre,
        es_default=True,
    )
    db_session.add(ref2)
    ins2 = Inscripcion(
        alumno_id=alumno2.id, curso_id=curso.id,
        fecha_inscripcion=date(2026, 1, 1), activa=True,
    )
    db_session.add(ins2)
    db_session.flush()
    generate_cuotas(db_session, ins2)
    db_session.flush()

    mov = make_movimiento(db_session, cuit=ref1.cuit_cuil, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.confianza == "medio"
    assert sug.metodo == "cuit_multiple"


def test_cuit_sin_cuotas_pendientes(db_session, alumno_con_referente):
    """CUIT matches but alumno has no pending cuotas → imputaciones empty, excedente = monto."""
    alumno, ref = alumno_con_referente
    mov = make_movimiento(db_session, cuit=ref.cuit_cuil, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.confianza == "alto"
    assert sug.imputaciones == []
    assert sug.excedente == Decimal("10000.00")


# ── Fuzzy nombre matching ─────────────────────────────────────────────────────

def test_fuzzy_nombre_medio(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Fuzzy nombre match on unique referente → confianza=medio."""
    _, ref = alumno_con_referente
    # ref.nombre_completo = "García Carlos"
    mov = make_movimiento(db_session, nombre="garcia carlos", cuit=None, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.metodo == "fuzzy_nombre"
    assert sug.confianza == "medio"
    assert len(sug.imputaciones) >= 1


def test_fuzzy_nombre_sin_match_bajo_threshold(db_session, inscripcion_con_cuotas):
    """Nombre with low similarity → no fuzzy match → sin_match."""
    mov = make_movimiento(db_session, nombre="xxxxxxxxxxx zzzzzzz", cuit=None, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.sin_match or sug.metodo != "fuzzy_nombre"


# ── Fuzzy referencia matching ─────────────────────────────────────────────────

def test_fuzzy_referencia_bajo(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Referencia matches alumno nombre+apellido → confianza=bajo."""
    alumno, _ = alumno_con_referente
    # alumno nombre="María", apellido="García"
    mov = make_movimiento(
        db_session, cuit=None, nombre=None,
        referencia="maria garcia", importe="10000.00",
    )
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.metodo == "fuzzy_referencia"
    assert sug.confianza == "bajo"


# ── Patron no-alumno ──────────────────────────────────────────────────────────

def test_patron_no_alumno(db_session):
    """Movement matching patron_no_alumno → es_no_alumno=True, confianza=alto."""
    patron = PatronNoAlumno(patron="eurolog", descripcion="Proveedor Eurolog")
    db_session.add(patron)
    db_session.flush()
    mov = make_movimiento(
        db_session, cuit=None, nombre="eurolog srl",
        concepto="pago a proveedores recibido - Eurolog srl 12345678901",
        importe="50000.00",
    )
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.es_no_alumno
    assert sug.confianza == "alto"
    assert sug.metodo == "patron_no_alumno"


# ── Sin match ─────────────────────────────────────────────────────────────────

def test_sin_match(db_session):
    """No matching cuit, nombre, referencia, patron → sin_match=True."""
    mov = make_movimiento(
        db_session, cuit="00000000000", nombre="desconocido total xyz",
        importe="10000.00",
    )
    sug = sugerir_conciliacion(db_session, mov.id)
    assert sug.sin_match
    assert sug.confianza == ""
    assert sug.metodo == ""


def test_movimiento_inexistente(db_session):
    with pytest.raises(ValueError, match="no encontrado"):
        sugerir_conciliacion(db_session, 99999)


# ── Aplicar conciliacion ──────────────────────────────────────────────────────

def test_aplicar_conciliacion(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """aplicar_conciliacion creates Pago+Imputacion, updates cuota state, marks movimiento conciliado."""
    from db.models import Cuota, Pago

    alumno, ref = alumno_con_referente
    # 5000 = exact matricula → fully paid in 1 imputacion
    mov = make_movimiento(db_session, cuit=ref.cuit_cuil, importe="5000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    imputaciones = [
        {"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
        for i in sug.imputaciones
    ]
    pago_id = aplicar_conciliacion(db_session, mov.id, imputaciones, operador="test")
    db_session.flush()

    pago = db_session.get(Pago, pago_id)
    assert pago is not None
    assert pago.monto == Decimal("5000.00")

    db_session.refresh(mov)
    assert mov.estado == EstadoMovimientoEnum.conciliado

    cuota_id = imputaciones[0]["cuota_id"]
    cuota = db_session.get(Cuota, cuota_id)
    assert cuota.estado == EstadoCuotaEnum.pagada
    assert cuota.saldo_pendiente == Decimal("0.00")


def test_aplicar_parcial(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Payment less than matricula(5000) → movimiento=conciliado, cuota=parcial."""
    from db.models import Cuota

    alumno, ref = alumno_con_referente
    mov = make_movimiento(db_session, cuit=ref.cuit_cuil, importe="4000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    imputaciones = [
        {"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
        for i in sug.imputaciones
    ]
    aplicar_conciliacion(db_session, mov.id, imputaciones)
    db_session.flush()

    db_session.refresh(mov)
    # total_imputado(4000) == mov.importe(4000) → conciliado
    assert mov.estado == EstadoMovimientoEnum.conciliado

    cuota = db_session.get(Cuota, imputaciones[0]["cuota_id"])
    assert cuota.estado == EstadoCuotaEnum.parcial
    assert cuota.saldo_pendiente == Decimal("1000.00")


# ── Revertir conciliacion ─────────────────────────────────────────────────────

def test_revertir_conciliacion(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """revertir restores cuota saldo and sets movimiento back to pendiente."""
    from db.models import Cuota

    alumno, ref = alumno_con_referente
    mov = make_movimiento(db_session, cuit=ref.cuit_cuil, importe="10000.00")
    sug = sugerir_conciliacion(db_session, mov.id)
    imputaciones = [
        {"cuota_id": i.cuota_id, "monto_imputado": i.monto_a_imputar}
        for i in sug.imputaciones
    ]
    aplicar_conciliacion(db_session, mov.id, imputaciones)
    db_session.flush()

    # imputaciones[0] = matricula (5000) → pagada; imputaciones[1] = cuota1 partial (5000)
    matricula_id = imputaciones[0]["cuota_id"]
    matricula = db_session.get(Cuota, matricula_id)
    assert matricula.estado == EstadoCuotaEnum.pagada

    revertir_conciliacion(db_session, mov.id)
    db_session.flush()

    db_session.refresh(mov)
    assert mov.estado == EstadoMovimientoEnum.pendiente

    matricula_after = db_session.get(Cuota, matricula_id)
    assert matricula_after.saldo_pendiente == Decimal("5000.00")  # original matricula amount
    assert matricula_after.estado == EstadoCuotaEnum.pendiente
