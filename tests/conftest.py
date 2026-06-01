import hashlib
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import (
    Alumno, Base, Curso, EstadoAlumnoEnum, EstadoMovimientoEnum,
    Inscripcion, ModalidadEnum, MovimientoBancario,
    ReferentePago, Sede, VinculoEnum,
)
from services.enrollment import generate_cuotas


@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def sede(db_session):
    obj = Sede(nombre="Sede Test", dia_vencimiento_default=10, activa=True)
    db_session.add(obj)
    db_session.flush()
    return obj


@pytest.fixture
def curso(db_session, sede):
    obj = Curso(
        sede_id=sede.id,
        nombre="Inglés Básico",
        modalidad=ModalidadEnum.presencial,
        monto_matricula=Decimal("5000.00"),
        monto_cuota_mensual=Decimal("10000.00"),
        cantidad_cuotas=3,
        activo=True,
    )
    db_session.add(obj)
    db_session.flush()
    return obj


@pytest.fixture
def alumno_con_referente(db_session, sede):
    alumno = Alumno(
        nombre="María",
        apellido="García",
        dni="30123456",
        sede_id=sede.id,
        estado=EstadoAlumnoEnum.activo,
    )
    db_session.add(alumno)
    db_session.flush()
    ref = ReferentePago(
        alumno_id=alumno.id,
        nombre_completo="García Carlos",
        cuit_cuil="20301234567",
        vinculo=VinculoEnum.padre,
        es_default=True,
    )
    db_session.add(ref)
    db_session.flush()
    return alumno, ref


@pytest.fixture
def inscripcion_con_cuotas(db_session, alumno_con_referente, curso):
    alumno, _ = alumno_con_referente
    ins = Inscripcion(
        alumno_id=alumno.id,
        curso_id=curso.id,
        fecha_inscripcion=date(2026, 1, 1),
        activa=True,
    )
    db_session.add(ins)
    db_session.flush()
    generate_cuotas(db_session, ins)
    db_session.flush()
    return ins


def make_movimiento(
    session,
    cuit: str | None = None,
    nombre: str | None = None,
    referencia: str | None = None,
    importe: str = "10000.00",
    concepto: str | None = None,
) -> MovimientoBancario:
    raw = concepto or f"Transferencia - De {nombre or 'unknown'} / {referencia or 'ref'} / {cuit or ''}"
    hash_val = hashlib.sha1(f"{raw}{importe}".encode()).hexdigest()
    mov = MovimientoBancario(
        fecha=date(2026, 4, 15),
        concepto_raw=raw,
        importe=Decimal(importe),
        cuit_detectado=cuit,
        nombre_pagador_detectado=nombre,
        referencia_detectada=referencia,
        tipo_movimiento="transferencia",
        hash_unico=hash_val,
        estado=EstadoMovimientoEnum.pendiente,
        fecha_importacion=datetime.utcnow(),
    )
    session.add(mov)
    session.flush()
    return mov
