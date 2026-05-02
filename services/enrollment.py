import calendar
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from db.models import (
    Curso, Cuota, EstadoCuotaEnum, Inscripcion,
    PrecioHistorico, TipoCuotaEnum,
)


def _apply_discounts(monto_base, descuento_pct, descuento_fijo) -> Decimal:
    result = Decimal(str(monto_base))
    if descuento_pct is not None:
        result = result * (1 - Decimal(str(descuento_pct)) / 100)
    if descuento_fijo is not None:
        result = result - Decimal(str(descuento_fijo))
    return max(result, Decimal("0.00")).quantize(Decimal("0.01"))


def generate_cuotas(session: Session, inscripcion: Inscripcion) -> list[Cuota]:
    curso = inscripcion.curso
    sede = curso.sede
    dia_vto = sede.dia_vencimiento_default

    cuotas: list[Cuota] = []

    # Matrícula — vence el día de inscripción
    monto_mat = _apply_discounts(
        curso.monto_matricula,
        inscripcion.descuento_porcentaje,
        inscripcion.descuento_fijo,
    )
    cuotas.append(Cuota(
        inscripcion_id=inscripcion.id,
        tipo=TipoCuotaEnum.matricula,
        numero_cuota=0,
        periodo=None,
        fecha_vencimiento=inscripcion.fecha_inscripcion,
        monto_original=curso.monto_matricula,
        monto_actualizado=monto_mat,
        estado=EstadoCuotaEnum.pendiente,
        saldo_pendiente=monto_mat,
    ))

    # Cuotas mensuales — empiezan desde el mes de inscripción
    start = inscripcion.fecha_inscripcion.replace(day=1)
    for i in range(curso.cantidad_cuotas):
        periodo_date = start + relativedelta(months=i)
        last_day = calendar.monthrange(periodo_date.year, periodo_date.month)[1]
        fecha_vto = date(periodo_date.year, periodo_date.month, min(dia_vto, last_day))
        monto = _apply_discounts(
            curso.monto_cuota_mensual,
            inscripcion.descuento_porcentaje,
            inscripcion.descuento_fijo,
        )
        cuotas.append(Cuota(
            inscripcion_id=inscripcion.id,
            tipo=TipoCuotaEnum.mensual,
            numero_cuota=i + 1,
            periodo=periodo_date.strftime("%Y-%m"),
            fecha_vencimiento=fecha_vto,
            monto_original=curso.monto_cuota_mensual,
            monto_actualizado=monto,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto,
        ))

    for c in cuotas:
        session.add(c)

    return cuotas


def save_price_history(session: Session, curso: Curso, motivo: str) -> None:
    session.add(PrecioHistorico(
        curso_id=curso.id,
        vigencia_desde=date.today(),
        monto_cuota=curso.monto_cuota_mensual,
        monto_matricula=curso.monto_matricula,
        motivo=motivo,
    ))


def recalculate_pending_cuotas(session: Session, curso_id: int) -> int:
    """Recalcula monto en cuotas pendientes no vencidas al nuevo precio del curso."""
    curso = session.get(Curso, curso_id)
    today = date.today()

    inscripciones = (
        session.query(Inscripcion)
        .filter(Inscripcion.curso_id == curso_id, Inscripcion.activa == True)
        .all()
    )

    updated = 0
    for ins in inscripciones:
        for cuota in ins.cuotas:
            if cuota.estado != EstadoCuotaEnum.pendiente:
                continue
            if cuota.fecha_vencimiento < today:
                continue
            if cuota.tipo == TipoCuotaEnum.mensual:
                base = curso.monto_cuota_mensual
            elif cuota.tipo == TipoCuotaEnum.matricula:
                base = curso.monto_matricula
            else:
                continue  # examen: precio fijo manual
            new_monto = _apply_discounts(base, ins.descuento_porcentaje, ins.descuento_fijo)
            cuota.monto_original = base
            cuota.monto_actualizado = new_monto
            cuota.saldo_pendiente = new_monto
            updated += 1

    return updated


def generate_exam_cuotas(session: Session, curso_id: int, monto: Decimal) -> int:
    """Genera cuota de derecho de examen para todos los inscriptos activos del curso."""
    curso = session.get(Curso, curso_id)
    today = date.today()
    next_month = today.replace(day=1) + relativedelta(months=1)
    sede = curso.sede
    last_day = calendar.monthrange(next_month.year, next_month.month)[1]
    fecha_vto = date(next_month.year, next_month.month, min(sede.dia_vencimiento_default, last_day))

    inscripciones = (
        session.query(Inscripcion)
        .filter(Inscripcion.curso_id == curso_id, Inscripcion.activa == True)
        .all()
    )

    count = 0
    for ins in inscripciones:
        if any(c.tipo == TipoCuotaEnum.examen for c in ins.cuotas):
            continue
        monto_final = _apply_discounts(monto, ins.descuento_porcentaje, ins.descuento_fijo)
        session.add(Cuota(
            inscripcion_id=ins.id,
            tipo=TipoCuotaEnum.examen,
            numero_cuota=None,
            periodo=today.strftime("%Y-%m"),
            fecha_vencimiento=fecha_vto,
            monto_original=monto,
            monto_actualizado=monto_final,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto_final,
        ))
        count += 1

    return count
