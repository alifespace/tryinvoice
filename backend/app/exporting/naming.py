from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from operator import attrgetter
from unicodedata import category as unicode_category
from uuid import UUID

from app.rules.dsl import NamingAndExport
from app.transactions.query import TransactionOut

_CENT = Decimal("0.01")
_INVALID_FILENAME_CHARS = frozenset('/\\:*?"<>|')
_ROLE_SUFFIX_SENTINEL = "\x00"


def amount_hyphen(amount: Decimal) -> str:
    if amount < 0:
        raise ValueError("金额不能为负数。")
    quantized = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    whole, cents = format(quantized, ".2f").split(".")
    return f"{int(whole)}-{cents}"


_TRANSACTION_KEY = attrgetter(
    "category", "region_scope", "region_detail", "tx_date", "merchant", "id"
)


def _render_collision_name(filename: str, role_suffix: str, count: int) -> str:
    suffix_position = filename.rfind(_ROLE_SUFFIX_SENTINEL)
    if suffix_position >= 0:
        if count > 1:
            filename = (
                f"{filename[:suffix_position]}_{count}{filename[suffix_position:]}"
            )
        return filename.replace(_ROLE_SUFFIX_SENTINEL, role_suffix)
    if count > 1:
        stem, dot, extension = filename.rpartition(".")
        if dot:
            filename = f"{stem}_{count}.{extension}"
        else:
            filename = f"{filename}_{count}"
    return filename


def _claim_unique_name(
    rendered_filename: str,
    role_suffix: str,
    seen: set[str],
    collisions: dict[str, int],
) -> str:
    filename = _render_collision_name(rendered_filename, role_suffix, 1)
    count = collisions.get(filename, 0) + 1
    candidate = _render_collision_name(rendered_filename, role_suffix, count)
    while candidate in seen:
        count += 1
        candidate = _render_collision_name(rendered_filename, role_suffix, count)
    collisions[filename] = count
    seen.add(candidate)
    return candidate


def plan_voucher_names(
    txs: Sequence[TransactionOut],
    exts: Mapping[UUID, str],
    naming: NamingAndExport,
) -> dict[UUID, str]:
    planned: dict[UUID, str] = {}
    seen: set[str] = set()
    collisions: dict[str, int] = {}
    documents = (
        (transaction, document)
        for transaction in sorted(txs, key=_TRANSACTION_KEY)
        for document in sorted(
            transaction.documents,
            key=lambda item: (item.role != "invoice", item.id),
        )
    )
    for transaction, document in documents:
        if document.id not in exts:
            raise ValueError(f"缺少文档扩展名：{document.id}")
        role_suffix = f"_{document.role}"
        pattern = naming.standard_pattern
        values = {
            "category": transaction.category,
            "date": transaction.tx_date.strftime("%Y%m%d"),
            "currency": transaction.currency,
            "amount_hyphen": amount_hyphen(transaction.original_amount),
            "role_suffix": _ROLE_SUFFIX_SENTINEL,
            "ext": exts[document.id],
            "direction": transaction.airport_direction or "",
            "iata": transaction.airport_iata or "",
        }
        if (
            transaction.category == "TRANS"
            and transaction.airport_direction
            and transaction.airport_iata
        ):
            pattern = naming.airport_transfer_pattern
        filename = pattern.format_map(values)
        filename = _claim_unique_name(filename, role_suffix, seen, collisions)
        planned[document.id] = filename
    return planned


def excel_filename(
    naming: NamingAndExport, expense_type_label: str, batch_date: date
) -> str:
    safe_label = "".join(
        character
        for character in expense_type_label
        if character not in _INVALID_FILENAME_CHARS
        and unicode_category(character) != "Cc"
    )
    return naming.excel_filename_pattern.format(
        expense_type_label=safe_label,
        batch_date=batch_date.isoformat(),
    )
