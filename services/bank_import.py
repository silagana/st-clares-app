import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from db.models import EstadoMovimientoEnum, MovimientoBancario
from utils.formatters import parse_importe_ar


# ── Extractors ─────────────────────────────────────────────────────────────

def _extract_tipo(concepto: str) -> str:
    """Extracts movement type prefix (everything before pagador marker)."""
    # Split before "De " or "Id debin" markers
    m = re.split(r'\s+-\s+(?=[Dd]e\s+|[Ii][Dd]\s+[Dd]ebin)', concepto, maxsplit=1)
    if len(m) > 1:
        return m[0].strip()
    return concepto.split(' - ')[0].strip()


def _extract_cuit(concepto: str) -> str | None:
    """Extracts CUIT: explicit 'cuit XXXXXXXXXXX' or last 11-digit sequence."""
    m = re.search(r'\bcuit\s+(\d{11})\b', concepto, re.IGNORECASE)
    if m:
        return m.group(1)
    matches = re.findall(r'\b(\d{11})\b', concepto)
    return matches[-1] if matches else None


def _extract_nombre_ref_subtipo(concepto: str) -> tuple[str | None, str | None, str | None]:
    """
    Extracts (nombre_pagador, referencia, subtipo) from concepto.
    Works on the lowercase concepto string.
    """
    m = re.search(r'[Dd]e\s+(.+)', concepto)
    if not m:
        return None, None, None

    after_de = m.group(1)

    # Strip trailing "/ CUIT" so it doesn't pollute referencia
    after_de = re.sub(r'\s*/\s*\d{11}\s*$', '', after_de).strip()

    # Split nombre from rest at first whitespace-surrounded slash
    # This lets "acosta/matias" stay intact (no surrounding spaces)
    parts = re.split(r'\s+/\s+', after_de, maxsplit=1)
    if len(parts) == 2:
        nombre_raw, rest = parts
    else:
        # Fallback: bare slash (e.g. "chir/ mercado pago /cuit")
        slash_parts = after_de.split('/', 1)
        nombre_raw = slash_parts[0]
        rest = slash_parts[1] if len(slash_parts) > 1 else ""

    nombre = nombre_raw.strip() or None
    referencia = rest.strip() or None
    subtipo: str | None = None

    if referencia:
        # subtipo is a 3-letter lowercase code at end: "vittoria - var"
        s_m = re.search(r'\s*-\s*([a-z]{3})\s*$', referencia)
        if s_m:
            subtipo = s_m.group(1)
            referencia = referencia[:s_m.start()].strip() or None

    return nombre, referencia, subtipo


# ── Parser ─────────────────────────────────────────────────────────────────

def parse_tsv(content: bytes) -> list[dict]:
    """
    Parse bank statement bytes (TSV, latin-1 encoded, CRLF).
    Returns list of dicts ready for DB insertion.
    """
    text = content.decode("latin-1").lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text), delimiter='\t')
    rows: list[dict] = []

    for raw in reader:
        fecha_str = (raw.get("Fecha") or "").strip()
        concepto_raw = (raw.get("Concepto") or "").strip()
        importe_str = (raw.get("Importe") or "").strip()

        if not fecha_str or not concepto_raw or not importe_str:
            continue

        # Parse date: D/M/YY or D/M/YYYY
        try:
            d_s, m_s, y_s = fecha_str.split("/")
            y_val = int(y_s)
            if y_val < 100:
                y_val += 2000
            fecha = date(y_val, int(m_s), int(d_s))
        except Exception:
            continue

        try:
            importe = parse_importe_ar(importe_str)
        except Exception:
            continue

        c = concepto_raw.lower()
        tipo = _extract_tipo(c)
        nombre, referencia, subtipo = _extract_nombre_ref_subtipo(c)
        cuit = _extract_cuit(c)

        # SHA1 of raw fields for idempotency
        hash_unico = hashlib.sha1(
            f"{fecha_str}|{concepto_raw}|{importe_str}".encode("utf-8")
        ).hexdigest()

        rows.append({
            "fecha": fecha,
            "concepto_raw": concepto_raw,
            "importe": importe,
            "tipo_movimiento": tipo,
            "nombre_pagador_detectado": nombre,
            "referencia_detectada": referencia,
            "subtipo": subtipo,
            "cuit_detectado": cuit,
            "hash_unico": hash_unico,
            "is_proveedor_candidate": "pago a proveedores" in tipo,
        })

    return rows


# ── DB import ──────────────────────────────────────────────────────────────

def import_movimientos(session: Session, content: bytes) -> tuple[int, int]:
    """
    Import parsed movements into DB. Idempotent via hash_unico.
    Returns (imported, skipped_duplicates).
    """
    rows = parse_tsv(content)
    imported = skipped = 0

    for row in rows:
        exists = session.query(MovimientoBancario).filter(
            MovimientoBancario.hash_unico == row["hash_unico"]
        ).first()
        if exists:
            skipped += 1
            continue

        estado = (
            EstadoMovimientoEnum.ignorado_no_alumno
            if row["is_proveedor_candidate"]
            else EstadoMovimientoEnum.pendiente
        )

        session.add(MovimientoBancario(
            fecha=row["fecha"],
            concepto_raw=row["concepto_raw"],
            importe=row["importe"],
            cuit_detectado=row["cuit_detectado"],
            nombre_pagador_detectado=row["nombre_pagador_detectado"],
            referencia_detectada=row["referencia_detectada"],
            subtipo=row["subtipo"],
            tipo_movimiento=row["tipo_movimiento"],
            hash_unico=row["hash_unico"],
            estado=estado,
            fecha_importacion=datetime.utcnow(),
        ))
        imported += 1

    return imported, skipped
