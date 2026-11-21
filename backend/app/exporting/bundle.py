import json
from collections.abc import Mapping, Sequence
from datetime import date
from hashlib import sha256
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.exporting.naming import amount_hyphen
from app.transactions.query import TransactionOut

_MANIFEST_NAME = ".processed_manifest.json"
_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_FIXED_EXTERNAL_ATTR = 0o100600 << 16


def transaction_fingerprint(tx: TransactionOut) -> str:
    value = "|".join(
        (
            tx.category,
            tx.tx_date.isoformat(),
            tx.currency,
            amount_hyphen(tx.original_amount),
            tx.merchant,
        )
    )
    return sha256(value.encode("utf-8")).hexdigest()


def build_manifest(
    batch_id: UUID,
    batch_date: date,
    txs: Sequence[TransactionOut],
    names: Mapping[UUID, str],
    sha256_by_doc: Mapping[UUID, str],
) -> bytes:
    entries = []
    for tx in txs:
        fingerprint = transaction_fingerprint(tx)
        for document in tx.documents:
            if document.id not in names or document.id not in sha256_by_doc:
                raise ValueError("缺少导出文件名或文档摘要。")
            entries.append(
                {
                    "document_id": str(document.id),
                    "transaction_id": str(tx.id),
                    "fingerprint": fingerprint,
                    "original_filename": document.original_filename,
                    "sha256": sha256_by_doc[document.id],
                    "export_filename": names[document.id],
                }
            )
    entries.sort(key=lambda entry: entry["export_filename"])
    manifest = {
        "version": 1,
        "batch_id": str(batch_id),
        "batch_date": batch_date.isoformat(),
        "entries": entries,
    }
    return (
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def export_zip_name(batch_id: UUID, batch_date: date) -> str:
    return f"export_{batch_date.isoformat()}_{batch_id.hex[:8]}.zip"


def _validate_member_name(name: str) -> None:
    if (
        not name
        or "/" in name
        or "\\" in name
        or name.startswith("..")
        or "\x00" in name
    ):
        raise ValueError("ZIP 文件名必须是安全的扁平文件名。")


def build_export_zip(
    workbook_name: str,
    workbook: bytes,
    vouchers: Mapping[str, bytes],
    manifest: bytes,
) -> bytes:
    names = [workbook_name, *vouchers, _MANIFEST_NAME]
    for name in names:
        _validate_member_name(name)
    if len(names) != len(set(names)):
        raise ValueError("ZIP 文件名不能重复。")

    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        for name, data in (
            (workbook_name, workbook),
            *sorted(vouchers.items()),
            (_MANIFEST_NAME, manifest),
        ):
            info = ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = _FIXED_EXTERNAL_ATTR
            archive.writestr(info, data)
    return output.getvalue()
