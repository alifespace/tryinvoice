from collections.abc import Mapping
from datetime import date
from typing import Literal, cast
from uuid import UUID

from psycopg import Connection
from pydantic import BaseModel, ConfigDict

from app.batches.jobs import start_job
from app.batches.service import BatchNotFound
from app.exporting.bundle import build_export_zip, build_manifest
from app.exporting.naming import excel_filename, plan_voucher_names
from app.exporting.workbook import build_workbook
from app.rules.dsl import validate_rules
from app.storage import ObjectStorage, StorageError, export_storage_key
from app.transactions.query import fetch_transactions

_ERROR = "导出失败。"
_FAIL_JOB = """UPDATE public.jobs SET status='failed',error_message=%s,
updated_at=now() WHERE id=%s"""
_DOCUMENTS = """SELECT DISTINCT d.id,d.original_filename,d.sha256_hash,d.storage_key
FROM public.raw_documents AS d JOIN public.transaction_documents AS td
ON td.raw_document_id=d.id AND td.user_id=%s
JOIN public.transactions AS t ON t.id=td.transaction_id AND t.user_id=%s
WHERE d.user_id=%s AND d.batch_id=%s AND t.batch_id=%s
AND td.link_role IN ('invoice','receipt')
"""


class ExportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    status: Literal["succeeded"]
    transactions: int
    vouchers: int
    download_url: str
    expires_in: int


class JobOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    batch_id: UUID
    job_type: str
    status: str
    progress: int
    error_message: str | None
    download_url: str | None
    expires_in: int | None


class ExportEmpty(Exception):
    pass


class JobNotFound(Exception):
    pass


def _voucher_extensions(rows: list[tuple[UUID, str, str, str]]) -> dict[UUID, str]:
    extensions: dict[UUID, str] = {}
    for document_id, _, _, storage_key in rows:
        filename = storage_key.rsplit("/", 1)[-1]
        if "." not in filename:
            raise StorageError
        extensions[document_id] = filename.rsplit(".", 1)[-1]
    return extensions


def _write_export(
    conn: Connection,
    user_id: UUID,
    batch_id: UUID,
    job_id: UUID,
    names: Mapping[UUID, str],
) -> None:
    key = export_storage_key(user_id, batch_id, job_id)
    conn.execute(
        "UPDATE public.jobs SET status='succeeded',progress=100,"
        "result_storage_key=%s,updated_at=now() WHERE id=%s",
        (key, job_id),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            "UPDATE public.transaction_documents SET export_filename=%s "
            "WHERE raw_document_id=%s AND user_id=%s "
            "AND link_role IN ('invoice','receipt') "
            "AND transaction_id IN (SELECT id FROM public.transactions "
            "WHERE user_id=%s AND batch_id=%s)",
            [
                (filename, document_id, user_id, user_id, batch_id)
                for document_id, filename in names.items()
            ],
        )


def export_batch(
    conn: Connection, storage: ObjectStorage, user_id: UUID, batch_id: UUID
) -> ExportOut:
    batch = conn.execute(
        "SELECT expense_type,batch_date,template_id,missing_alerts "
        "FROM public.reimbursement_batches WHERE id=%s AND user_id=%s",
        (batch_id, user_id),
    ).fetchone()
    if batch is None:
        raise BatchNotFound
    job_id = start_job(conn, user_id, batch_id, "export_bundle")

    try:
        rules_row = conn.execute(
            "SELECT rules_json FROM public.rule_templates WHERE id=%s",
            (cast(UUID, batch[2]),),
        ).fetchone()
        if rules_row is None:
            raise RuntimeError(_ERROR)
        rules = validate_rules(cast(Mapping[str, object], rules_row[0]))
        transactions = fetch_transactions(conn, user_id, batch_id)
        if not transactions:
            raise ExportEmpty
        voucher_rows = conn.execute(
            _DOCUMENTS, (user_id, user_id, user_id, batch_id, batch_id)
        ).fetchall()
        extensions = _voucher_extensions(voucher_rows)
        names = plan_voucher_names(transactions, extensions, rules.naming_and_export)
        voucher_contents: dict[str, bytes] = {}
        hashes: dict[UUID, str] = {}
        for document_id, _, digest, storage_key in voucher_rows:
            data = storage.get(storage_key)
            if data is None:
                raise StorageError
            if document_id in names:
                voucher_contents[names[document_id]] = data
                hashes[document_id] = digest
        expense_labels = {item.code: item.label for item in rules.expense_types}
        workbook_name = excel_filename(
            rules.naming_and_export,
            expense_labels[cast(str, batch[0])],
            cast(date, batch[1]),
        )
        workbook = build_workbook(
            transactions, names, rules, cast(list[Mapping[str, str]], batch[3])
        )
        manifest = build_manifest(
            batch_id, cast(date, batch[1]), transactions, names, hashes
        )
        archive = build_export_zip(workbook_name, workbook, voucher_contents, manifest)
        result_key = export_storage_key(user_id, batch_id, job_id)
        storage.put(result_key, archive, "application/zip")
        url, expires = storage.presign_get(result_key)
        with conn.transaction():
            _write_export(conn, user_id, batch_id, job_id, names)
        return ExportOut(
            job_id=job_id,
            status="succeeded",
            transactions=len(transactions),
            vouchers=len(names),
            download_url=url,
            expires_in=expires,
        )
    except Exception:
        conn.execute(_FAIL_JOB, (_ERROR, job_id))
        raise


def get_job(
    conn: Connection, storage: ObjectStorage, user_id: UUID, job_id: UUID
) -> JobOut:
    row = conn.execute(
        "SELECT id,batch_id,job_type,status,progress,error_message,result_storage_key "
        "FROM public.jobs WHERE id=%s AND user_id=%s",
        (job_id, user_id),
    ).fetchone()
    if row is None:
        raise JobNotFound
    url = None
    expires = None
    if row[2] == "export_bundle" and row[3] == "succeeded" and row[6]:
        url, expires = storage.presign_get(cast(str, row[6]))
    return JobOut.model_validate(
        dict(zip(JobOut.model_fields, (*row[:6], url, expires), strict=True))
    )
