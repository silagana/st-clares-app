from decimal import Decimal

import pytest

from services.reconciliation import fifo_distribuir


def test_fifo_exacto(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Payment covers matricula(5000) + partial cuota1(5000) → 2 imputaciones."""
    alumno, _ = alumno_con_referente
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("10000.00"))
    assert len(imps) == 2
    assert sum(i.monto_a_imputar for i in imps) == Decimal("10000.00")
    assert imps[0].monto_a_imputar == Decimal("5000.00")   # matricula completa
    assert imps[0].saldo_restante_cuota == Decimal("0.00")
    assert imps[1].monto_a_imputar == Decimal("5000.00")   # parcial cuota1
    assert exc == Decimal("0.00")


def test_fifo_parcial(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Partial payment less than matricula(5000) → 1 imputacion."""
    alumno, _ = alumno_con_referente
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("4000.00"))
    assert len(imps) == 1
    assert imps[0].monto_a_imputar == Decimal("4000.00")
    assert imps[0].saldo_restante_cuota == Decimal("1000.00")
    assert exc == Decimal("0.00")


def test_fifo_excedente(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Payment exceeds total owed: excedente returned."""
    alumno, _ = alumno_con_referente
    # curso has 3 cuotas * 10000 + 5000 matricula = 35000 total
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("40000.00"))
    total_imputado = sum(i.monto_a_imputar for i in imps)
    assert total_imputado == Decimal("35000.00")
    assert exc == Decimal("5000.00")


def test_fifo_multi_cuota(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Payment spans multiple cuotas."""
    alumno, _ = alumno_con_referente
    # 5000 matricula + 10000 = 15000 covers matricula + first cuota
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("15000.00"))
    assert len(imps) == 2
    assert sum(i.monto_a_imputar for i in imps) == Decimal("15000.00")
    assert exc == Decimal("0.00")


def test_fifo_cubre_todo(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Payment covers all cuotas exactly."""
    alumno, _ = alumno_con_referente
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("35000.00"))
    assert len(imps) == 4  # matricula + 3 mensual
    assert sum(i.monto_a_imputar for i in imps) == Decimal("35000.00")
    assert exc == Decimal("0.00")


def test_fifo_sin_cuotas(db_session, alumno_con_referente):
    """Alumno with no inscriptions: empty imputaciones, full excedente."""
    alumno, _ = alumno_con_referente
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("10000.00"))
    assert imps == []
    assert exc == Decimal("10000.00")


def test_fifo_alumno_inexistente(db_session):
    """Non-existent alumno_id: no crash, empty result."""
    imps, exc = fifo_distribuir(db_session, [99999], Decimal("10000.00"))
    assert imps == []
    assert exc == Decimal("10000.00")


def test_fifo_monto_cero(db_session, inscripcion_con_cuotas, alumno_con_referente):
    """Zero monto: no imputaciones, zero excedente."""
    alumno, _ = alumno_con_referente
    imps, exc = fifo_distribuir(db_session, [alumno.id], Decimal("0.00"))
    assert imps == []
    assert exc == Decimal("0.00")
