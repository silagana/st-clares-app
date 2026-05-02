from datetime import date
from decimal import Decimal

import pytest

from services.bank_import import import_movimientos, parse_tsv

# Exact sample from spec (latin-1, CRLF)
SAMPLE = (
    "Fecha\tConcepto\tImporte\r\n"
    "23/4/26\tCredito transf online banking emp - De repuestos diesel roal  / cuota        - cuo / 30717471578\t240.000,00\r\n"
    "10/4/26\tCredito transf por online banking - De claudia vanesa velasqu / vittoria     - var / 27939812321\t140.000,00\r\n"
    "14/4/26\tTransf recibida cvu dif titular - De darwin enriq sanchez chir/ mercado pago   /20960328575\t237.500,00\r\n"
    "1/4/26\tPago a proveedores recibido - Eurolog srl                   30707823786 03 0291666\t900.000,00\r\n"
    "16/4/26\tTransferencia ctas mobile banking - De luis enrique miranda l / nicol s mira - cuo / 23926457969\t140.000,00\r\n"
    "7/4/26\tTransferencia recibida - credin - Id debin loejwv9jzel7ddjz2qmd0g cuit 27300358395\t145.000,00\r\n"
).encode("latin-1")


# ── Parser tests ───────────────────────────────────────────────────────────

def test_parse_count():
    assert len(parse_tsv(SAMPLE)) == 6


def test_parse_importes():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["importe"] == Decimal("240000.00")
    assert rows[2]["importe"] == Decimal("237500.00")
    assert rows[3]["importe"] == Decimal("900000.00")


def test_parse_fechas():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["fecha"] == date(2026, 4, 23)
    assert rows[3]["fecha"] == date(2026, 4, 1)
    assert rows[5]["fecha"] == date(2026, 4, 7)


def test_cuit_extraction():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["cuit_detectado"] == "30717471578"
    assert rows[1]["cuit_detectado"] == "27939812321"
    assert rows[2]["cuit_detectado"] == "20960328575"
    assert rows[3]["cuit_detectado"] == "30707823786"  # proveedor
    assert rows[4]["cuit_detectado"] == "23926457969"
    assert rows[5]["cuit_detectado"] == "27300358395"  # DEBIN


def test_nombre_extraction():
    rows = parse_tsv(SAMPLE)
    assert "repuestos diesel roal" in rows[0]["nombre_pagador_detectado"]
    assert "claudia vanesa velasqu" in rows[1]["nombre_pagador_detectado"]
    assert "darwin enriq sanchez chir" in rows[2]["nombre_pagador_detectado"]
    assert rows[3]["nombre_pagador_detectado"] is None   # proveedor sin "De "
    assert "luis enrique miranda l" in rows[4]["nombre_pagador_detectado"]
    assert rows[5]["nombre_pagador_detectado"] is None   # DEBIN sin "De "


def test_referencia_extraction():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["referencia_detectada"] == "cuota"
    assert rows[1]["referencia_detectada"] == "vittoria"
    assert rows[2]["referencia_detectada"] == "mercado pago"
    assert rows[4]["referencia_detectada"] == "nicol s mira"


def test_subtipo_extraction():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["subtipo"] == "cuo"
    assert rows[1]["subtipo"] == "var"
    assert rows[2]["subtipo"] is None
    assert rows[4]["subtipo"] == "cuo"


def test_tipo_movimiento():
    rows = parse_tsv(SAMPLE)
    assert rows[0]["tipo_movimiento"] == "credito transf online banking emp"
    assert rows[3]["tipo_movimiento"] == "pago a proveedores recibido"
    assert rows[5]["tipo_movimiento"] == "transferencia recibida - credin"


def test_proveedor_flagged():
    rows = parse_tsv(SAMPLE)
    assert rows[3]["is_proveedor_candidate"] is True
    assert rows[0]["is_proveedor_candidate"] is False
    assert rows[5]["is_proveedor_candidate"] is False


def test_hash_unique():
    rows = parse_tsv(SAMPLE)
    hashes = [r["hash_unico"] for r in rows]
    assert len(hashes) == len(set(hashes))


def test_hash_deterministic():
    rows_a = parse_tsv(SAMPLE)
    rows_b = parse_tsv(SAMPLE)
    assert [r["hash_unico"] for r in rows_a] == [r["hash_unico"] for r in rows_b]


# ── DB import tests ────────────────────────────────────────────────────────

def test_import_count(db_session):
    imported, skipped = import_movimientos(db_session, SAMPLE)
    assert imported == 6
    assert skipped == 0


def test_import_idempotent(db_session):
    import_movimientos(db_session, SAMPLE)
    db_session.flush()
    imported2, skipped2 = import_movimientos(db_session, SAMPLE)
    assert imported2 == 0
    assert skipped2 == 6


def test_import_partial_idempotent(db_session):
    # Import once, then re-import same file with 1 extra row
    import_movimientos(db_session, SAMPLE)
    db_session.flush()

    extra = (
        "Fecha\tConcepto\tImporte\r\n"
        "23/4/26\tCredito transf online banking emp - De repuestos diesel roal  / cuota        - cuo / 30717471578\t240.000,00\r\n"
        "5/5/26\tCredito transf por online banking - De nuevo pagador / ref - var / 20111222333\t50.000,00\r\n"
    ).encode("latin-1")

    imported3, skipped3 = import_movimientos(db_session, extra)
    assert imported3 == 1
    assert skipped3 == 1


def test_proveedor_estado(db_session):
    from db.models import EstadoMovimientoEnum, MovimientoBancario
    import_movimientos(db_session, SAMPLE)
    db_session.flush()
    prov = db_session.query(MovimientoBancario).filter(
        MovimientoBancario.tipo_movimiento == "pago a proveedores recibido"
    ).first()
    assert prov is not None
    assert prov.estado == EstadoMovimientoEnum.ignorado_no_alumno


def test_normal_estado(db_session):
    from db.models import EstadoMovimientoEnum, MovimientoBancario
    import_movimientos(db_session, SAMPLE)
    db_session.flush()
    normal = db_session.query(MovimientoBancario).filter(
        MovimientoBancario.cuit_detectado == "30717471578"
    ).first()
    assert normal.estado == EstadoMovimientoEnum.pendiente
