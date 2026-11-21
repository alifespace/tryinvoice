from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any, Self
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.batches.processing import _load_template
from app.rules.dsl import RuleTemplateDSL
from app.transactions.query import TransactionOut, fetch_transactions

_CENT = Decimal("0.01")
_MONEY = Context(prec=28, rounding=ROUND_HALF_UP)
_INVALID = "交易修订无效"
_CURRENT_FIELDS = (
    "category",
    "split_count",
    "tip_amount",
    "original_amount",
    "notes",
    "payment_status",
    "flags",
)
_LINK_FIELDS = {"invoice_doc_id", "receipt_doc_id"}


class TransactionNotFound(Exception):
    pass


class InvalidTransactionPatch(Exception):
    pass


class LinkedDocumentConflict(Exception):
    pass


class TransactionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(None, min_length=1, max_length=32)
    split_count: int | None = Field(None, ge=1, le=99)
    tip_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    notes: str | None = Field(None, max_length=1000)
    invoice_doc_id: UUID | None = None
    receipt_doc_id: UUID | None = None

    @model_validator(mode="after")
    def has_changes(self) -> Self:
        if not self.model_fields_set or any(
            key in self.model_fields_set and getattr(self, key) is None
            for key in ("category", "split_count", "tip_amount", "notes")
        ):
            raise ValueError(_INVALID)
        return self


def _lock_transaction(
    conn: Connection, user_id: UUID, tx_id: UUID
) -> tuple[UUID, UUID, tuple[Any, ...]]:
    owner = conn.execute(
        "SELECT batch_id FROM public.transactions WHERE id=%s AND user_id=%s",
        (tx_id, user_id),
    ).fetchone()
    if owner is None:
        raise TransactionNotFound
    batch_id = owner[0]
    batch = conn.execute(
        "SELECT template_id FROM public.reimbursement_batches "
        "WHERE id=%s AND user_id=%s FOR UPDATE",
        (batch_id, user_id),
    ).fetchone()
    row = conn.execute(
        "SELECT category,split_count,tip_amount,original_amount,notes,"
        "payment_status,flags FROM public.transactions "
        "WHERE id=%s AND user_id=%s FOR UPDATE",
        (tx_id, user_id),
    ).fetchone()
    if batch is None or row is None:
        raise TransactionNotFound
    return batch_id, batch[0], row


def _merge_fields(current: dict[str, Any], body: TransactionPatch) -> dict[str, Any]:
    for field in ("category", "split_count", "tip_amount", "notes"):
        if field in body.model_fields_set:
            current[field] = getattr(body, field)
    return current


def _validate_fields(
    template: RuleTemplateDSL, category: str, tip: Decimal, original: Decimal
) -> None:
    if category not in {item.code for item in template.categories} or tip > original:
        raise InvalidTransactionPatch


def _load_links(conn: Connection, user_id: UUID, tx_id: UUID) -> dict[str, UUID]:
    rows = conn.execute(
        "SELECT link_role,raw_document_id FROM public.transaction_documents "
        "WHERE transaction_id=%s AND user_id=%s "
        "AND link_role IN ('invoice','receipt')",
        (tx_id, user_id),
    ).fetchall()
    return dict(rows)


def _target_links(before: dict[str, UUID], body: TransactionPatch) -> dict[str, UUID]:
    docs = before.copy()
    for role in ("invoice", "receipt"):
        field = f"{role}_doc_id"
        if field not in body.model_fields_set:
            continue
        doc_id = getattr(body, field)
        if doc_id is None:
            docs.pop(role, None)
        else:
            docs[role] = doc_id
    if not docs or len(set(docs.values())) != len(docs):
        raise InvalidTransactionPatch
    return docs


def _check_new_link(
    conn: Connection,
    user_id: UUID,
    batch_id: UUID,
    tx_id: UUID,
    role: str,
    doc_id: UUID,
) -> None:
    row = conn.execute(
        "SELECT batch_id,ocr_status,duplicate_of_id,doc_role "
        "FROM public.raw_documents WHERE id=%s AND user_id=%s",
        (doc_id, user_id),
    ).fetchone()
    expected = "invoice" if role == "invoice" else "payment_receipt"
    if row is None or row != (batch_id, "completed", None, expected):
        raise InvalidTransactionPatch
    if conn.execute(
        "SELECT 1 FROM public.transaction_documents WHERE raw_document_id=%s "
        "AND user_id=%s AND transaction_id<>%s",
        (doc_id, user_id, tx_id),
    ).fetchone():
        raise LinkedDocumentConflict


def _payment_status(docs: dict[str, UUID]) -> str:
    if "invoice" in docs and "receipt" in docs:
        return "paired"
    if "invoice" in docs:
        return "missing_payment"
    return "missing_invoice"


def _plan_links(
    conn: Connection, user_id: UUID, batch_id: UUID, tx_id: UUID, body: TransactionPatch
) -> tuple[dict[str, UUID], bool, str]:
    if not (_LINK_FIELDS & body.model_fields_set):
        return {}, False, ""
    before = _load_links(conn, user_id, tx_id)
    docs = _target_links(before, body)
    for role, doc_id in docs.items():
        if before.get(role) != doc_id:
            _check_new_link(conn, user_id, batch_id, tx_id, role, doc_id)
    changed = docs != before
    return docs, changed, _payment_status(docs)


def _claim(original: Decimal, tip: Decimal, split: int) -> Decimal:
    with localcontext(_MONEY):
        return ((original - tip) / split).quantize(_CENT, rounding=ROUND_HALF_UP)


def _flags(
    current: list[str],
    category: str,
    split: int,
    tip: Decimal,
    status: str,
    highlight: str,
) -> list[str]:
    managed = {
        highlight,
        "MEAL_SPLIT",
        "MISSING_PAYMENT",
        "MISSING_INVOICE",
        "UNKNOWN_CATEGORY",
    }
    result = [flag for flag in current if flag not in managed]
    result.extend(
        flag
        for condition, flag in (
            (tip > 0, highlight),
            (split > 1, "MEAL_SPLIT"),
            (status == "missing_payment", "MISSING_PAYMENT"),
            (status == "missing_invoice", "MISSING_INVOICE"),
            (category == "UNKNOWN", "UNKNOWN_CATEGORY"),
        )
        if condition
    )
    return list(dict.fromkeys(result))


def _replace_links(
    conn: Connection, user_id: UUID, tx_id: UUID, docs: dict[str, UUID]
) -> None:
    conn.execute(
        "DELETE FROM public.transaction_documents "
        "WHERE transaction_id=%s AND user_id=%s",
        (tx_id, user_id),
    )
    for role, doc_id in docs.items():
        conn.execute(
            "INSERT INTO public.transaction_documents "
            "(transaction_id,raw_document_id,user_id,link_role) "
            "VALUES (%s,%s,%s,%s)",
            (tx_id, doc_id, user_id, role),
        )


def patch_transaction(
    conn: Connection, user_id: UUID, tx_id: UUID, body: TransactionPatch
) -> TransactionOut:
    with conn.transaction():
        batch_id, template_id, row = _lock_transaction(conn, user_id, tx_id)
        current = dict(zip(_CURRENT_FIELDS, row, strict=True))
        values = _merge_fields(current, body)
        category, split, tip, original = (
            values["category"],
            values["split_count"],
            values["tip_amount"],
            values["original_amount"],
        )
        template = _load_template(conn, template_id)
        _validate_fields(template, category, tip, original)
        docs, links_changed, link_status = _plan_links(
            conn, user_id, batch_id, tx_id, body
        )
        payment = link_status if links_changed else values["payment_status"]
        claim = _claim(original, tip, split)
        highlight = template.compliance_rules.exclude_ride_hailing_tips.highlight_flag
        flags = _flags(values["flags"], category, split, tip, payment, highlight)
        conn.execute(
            "UPDATE public.transactions SET category=%s,split_count=%s,tip_amount=%s,"
            "claim_amount=%s,notes=%s,payment_status=%s,flags=%s,is_user_verified=true,"
            "updated_at=now() WHERE id=%s AND user_id=%s",
            (
                category,
                split,
                tip,
                claim,
                values["notes"],
                payment,
                Jsonb(flags),
                tx_id,
                user_id,
            ),
        )
        if links_changed:
            _replace_links(conn, user_id, tx_id, docs)
        return fetch_transactions(conn, user_id, batch_id, tx_id)[0]
