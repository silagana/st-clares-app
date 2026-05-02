"""initial

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sede",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("encargada_nombre", sa.String(120)),
        sa.Column("encargada_telefono", sa.String(30)),
        sa.Column("dia_vencimiento_default", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nombre"),
    )

    op.create_table(
        "curso",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sede_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("nivel", sa.String(60)),
        sa.Column("modalidad", sa.Enum("presencial", "online", name="modalidadenum"), nullable=False),
        sa.Column("monto_matricula", sa.Numeric(14, 2), nullable=False),
        sa.Column("monto_cuota_mensual", sa.Numeric(14, 2), nullable=False),
        sa.Column("cantidad_cuotas", sa.Integer(), nullable=False),
        sa.Column("derecho_examen_monto", sa.Numeric(14, 2)),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["sede_id"], ["sede.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "precio_historico",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("curso_id", sa.Integer(), nullable=False),
        sa.Column("vigencia_desde", sa.Date(), nullable=False),
        sa.Column("monto_cuota", sa.Numeric(14, 2), nullable=False),
        sa.Column("monto_matricula", sa.Numeric(14, 2), nullable=False),
        sa.Column("motivo", sa.String(255)),
        sa.ForeignKeyConstraint(["curso_id"], ["curso.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "alumno",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("apellido", sa.String(80), nullable=False),
        sa.Column("dni", sa.String(15), nullable=False),
        sa.Column("fecha_nacimiento", sa.Date()),
        sa.Column("telefono", sa.String(30)),
        sa.Column("email", sa.String(120)),
        sa.Column("sede_id", sa.Integer(), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("activo", "suspendido", "baja", name="estadoalumnoenum"),
            nullable=False,
            server_default="activo",
        ),
        sa.Column("fecha_estado", sa.Date()),
        sa.Column("motivo_estado", sa.String(255)),
        sa.Column("observaciones", sa.Text()),
        sa.ForeignKeyConstraint(["sede_id"], ["sede.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dni"),
    )

    op.create_table(
        "referente_pago",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alumno_id", sa.Integer(), nullable=False),
        sa.Column("nombre_completo", sa.String(160), nullable=False),
        sa.Column("cuit_cuil", sa.String(13), nullable=False),
        sa.Column("telefono", sa.String(30)),
        sa.Column(
            "vinculo",
            sa.Enum("padre", "madre", "empresa", "otro", "mismo_alumno", name="vinculoenum"),
            nullable=False,
        ),
        sa.Column("es_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["alumno_id"], ["alumno.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "inscripcion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alumno_id", sa.Integer(), nullable=False),
        sa.Column("curso_id", sa.Integer(), nullable=False),
        sa.Column("fecha_inscripcion", sa.Date(), nullable=False),
        sa.Column("descuento_porcentaje", sa.Numeric(5, 2)),
        sa.Column("descuento_fijo", sa.Numeric(14, 2)),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["alumno_id"], ["alumno.id"]),
        sa.ForeignKeyConstraint(["curso_id"], ["curso.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cuota",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inscripcion_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum("matricula", "mensual", "examen", name="tipocuotaenum"),
            nullable=False,
        ),
        sa.Column("numero_cuota", sa.Integer()),
        sa.Column("periodo", sa.String(7)),
        sa.Column("fecha_vencimiento", sa.Date(), nullable=False),
        sa.Column("monto_original", sa.Numeric(14, 2), nullable=False),
        sa.Column("monto_actualizado", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "estado",
            sa.Enum("pendiente", "parcial", "pagada", "condonada", name="estadocuotaenum"),
            nullable=False,
            server_default="pendiente",
        ),
        sa.Column("saldo_pendiente", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["inscripcion_id"], ["inscripcion.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "movimiento_bancario",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("concepto_raw", sa.Text(), nullable=False),
        sa.Column("importe", sa.Numeric(14, 2), nullable=False),
        sa.Column("cuit_detectado", sa.String(13)),
        sa.Column("nombre_pagador_detectado", sa.String(160)),
        sa.Column("referencia_detectada", sa.String(160)),
        sa.Column("subtipo", sa.String(10)),
        sa.Column("tipo_movimiento", sa.String(120)),
        sa.Column("hash_unico", sa.String(40), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "pendiente", "conciliado", "parcial", "ignorado_no_alumno",
                name="estadomovimientoenum",
            ),
            nullable=False,
            server_default="pendiente",
        ),
        sa.Column("fecha_importacion", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hash_unico"),
    )

    op.create_table(
        "pago",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("monto", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "medio",
            sa.Enum("transferencia", "efectivo", name="mediopagoenum"),
            nullable=False,
        ),
        sa.Column("movimiento_bancario_id", sa.Integer()),
        sa.Column("operador_efectivo", sa.String(80)),
        sa.Column("observaciones", sa.Text()),
        sa.ForeignKeyConstraint(["movimiento_bancario_id"], ["movimiento_bancario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "imputacion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pago_id", sa.Integer(), nullable=False),
        sa.Column("cuota_id", sa.Integer(), nullable=False),
        sa.Column("monto_imputado", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["cuota_id"], ["cuota.id"]),
        sa.ForeignKeyConstraint(["pago_id"], ["pago.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "patron_no_alumno",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patron", sa.String(160), nullable=False),
        sa.Column("descripcion", sa.String(255)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patron"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("accion", sa.String(60), nullable=False),
        sa.Column("entidad", sa.String(60), nullable=False),
        sa.Column("entidad_id", sa.Integer()),
        sa.Column("detalle_json", sa.Text()),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("patron_no_alumno")
    op.drop_table("imputacion")
    op.drop_table("pago")
    op.drop_table("movimiento_bancario")
    op.drop_table("cuota")
    op.drop_table("inscripcion")
    op.drop_table("referente_pago")
    op.drop_table("alumno")
    op.drop_table("precio_historico")
    op.drop_table("curso")
    op.drop_table("sede")
