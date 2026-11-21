from dataclasses import replace
from uuid import uuid4

import pytest

from app.batches.sync import sync_transactions
from tests.test_process_batch import _add, _draft, _ocr

pytest_plugins = ("tests.test_process_batch",)


def _draft_for(document, merchant, **changes):
    return replace(_draft(document, merchant), **changes)


def _transaction_row(conn, user, batch):
    return conn.execute(
        "INSERT INTO public.transactions (user_id,batch_id,category,region_scope,"
        "region_detail,tx_date,merchant,currency,original_amount,claim_amount,"
        "payment_status) VALUES (%s,%s,'EAT','in_scope','美洲','2026-10-02','x',"
        "'USD',1,1,'paired') RETURNING id",
        (user, batch),
    ).fetchone()[0]


def _batch_for_user(conn, user, batch):
    return conn.execute(
        "INSERT INTO public.reimbursement_batches "
        "(user_id,template_id,expense_type,title,batch_date) "
        "SELECT %s,template_id,expense_type,title,batch_date FROM "
        "public.reimbursement_batches WHERE id=%s RETURNING id",
        (user, batch),
    ).fetchone()[0]


def _transaction_state(conn, tx):
    return conn.execute(
        "SELECT to_jsonb(t)||jsonb_build_object('links',COALESCE((SELECT "
        "jsonb_agg(jsonb_build_array(raw_document_id,link_role) ORDER BY link_role) "
        "FROM public.transaction_documents WHERE transaction_id=t.id),'[]'::jsonb)) "
        "FROM public.transactions t WHERE id=%s",
        (tx,),
    ).fetchone()


def test_transaction_groups_and_details(process_api: tuple) -> None:
    client, conn, _, user, batch, *_ = process_api
    path = f"/api/v1/batches/{batch}/transactions"
    assert client.get(path).json()["groups"] == []
    docs = [_add(conn, user, batch, "image/jpeg")[0] for _ in range(6)]
    pair = replace(
        _draft(docs[0], "Cafe", True, ("TIP_EXCLUDED",)), receipt_doc_id=str(docs[1])
    )
    other = _draft_for(docs[2], "Other", region_scope="out_of_scope")
    airport = _draft_for(
        docs[3],
        "LAX",
        category="TRANS",
        airport_direction="To",
        airport_iata="LAX",
        payment_status="missing_payment",
    )
    alpha = _draft_for(docs[4], "Alpha")
    mainland = _draft_for(docs[5], "Mainland", region_detail="大陆")
    sync_transactions(conn, user, batch, (pair, other, airport, alpha, mainland))
    conn.execute(
        "UPDATE public.transactions SET is_user_verified=true,notes='reviewed' "
        "WHERE id=(SELECT transaction_id FROM public.transaction_documents "
        "WHERE raw_document_id=%s)",
        (docs[0],),
    )
    conn.execute(
        "UPDATE public.reimbursement_batches SET status='review_required',"
        'missing_alerts=\'[{"code":"MANUAL"}]\'::jsonb WHERE id=%s',
        (batch,),
    )
    data = client.get(path).json()
    assert [g["category"] for g in data["groups"]] == ["EAT", "TRANS"]
    eat = data["groups"][0]
    assert [s["region_scope"] for s in eat["scopes"]] == ["in_scope", "out_of_scope"]
    regions = eat["scopes"][0]["regions"]
    assert [r["region_detail"] for r in regions] == ["大陆", "美洲"]
    rows = regions[1]["transactions"]
    assert [tx["merchant"] for tx in rows] == ["Alpha", "Cafe"]
    tx = rows[1]
    assert tx["original_amount"] == tx["claim_amount"] == "8.00" and tx["flags"] == [
        "TIP_EXCLUDED"
    ]
    assert (tx["notes"], tx["is_user_verified"]) == ("reviewed", True)
    assert [d["role"] for d in tx["documents"]] == ["invoice", "receipt"]
    assert (rows[0]["notes"], rows[0]["is_user_verified"]) == ("", False)
    receipt = eat["scopes"][1]["regions"][0]["transactions"][0]["documents"]
    assert [d["role"] for d in receipt] == ["receipt"]
    tx = data["groups"][1]["scopes"][0]["regions"][0]["transactions"][0]
    assert tx["payment_status"] == "missing_payment" and (
        tx["airport_direction"],
        tx["airport_iata"],
    ) == ("To", "LAX")
    assert data["status"] == "review_required"
    assert data["missing_alerts"] == [{"code": "MANUAL"}]
    payload = str(data)
    assert all(
        k not in payload for k in ("storage_key", "sha256_hash", "extracted_json")
    )


def test_transaction_auth_and_tenant_isolation(process_api: tuple) -> None:
    client, conn, users, _, batch, *_ = process_api
    path = f"/api/v1/batches/{batch}/transactions"
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    doc, _ = _add(conn, users[1], batch, "image/jpeg")
    sync_transactions(conn, users[1], batch, (_draft(doc, "private"),))
    foreign = conn.execute(
        "INSERT INTO public.reimbursement_batches "
        "(user_id,template_id,expense_type,title,batch_date) "
        "SELECT %s,template_id,expense_type,title,batch_date "
        "FROM public.reimbursement_batches WHERE id=%s RETURNING id",
        (users[1], batch),
    ).fetchone()[0]
    a, b = (client.get(f"/api/v1/batches/{i}/transactions") for i in (foreign, uuid4()))
    assert a.status_code == b.status_code == 404 and a.text == b.text
    assert "private" not in client.get(path).text


def _processed_pair(process_api: tuple) -> tuple:
    client, conn, users, user, batch, *_ = process_api
    invoice, _ = _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr(
            "Invoice",
            "Merchant: Cafe",
            "Date: 2026-10-02",
            "Total: $20.01",
            "Tip: 3.00",
        ),
    )
    receipt, _ = _add(
        conn,
        user,
        batch,
        "image/jpeg",
        _ocr("Receipt", "Merchant: Cafe", "Date: 2026-10-02", "Total: $20.01"),
    )
    client.post(f"/api/v1/batches/{batch}/process")
    tx = conn.execute(
        "SELECT transaction_id FROM public.transaction_documents "
        "WHERE raw_document_id=%s",
        (invoice,),
    ).fetchone()[0]
    return client, conn, users, user, batch, tx, invoice, receipt


def test_patch_transaction_updates_and_survives_reprocess(process_api: tuple) -> None:
    client, conn, _, user, batch, tx, invoice, receipt = _processed_pair(process_api)
    path = f"/api/v1/transactions/{tx}"
    data = client.patch(
        path,
        json={
            "category": "DRINK",
            "split_count": 2,
            "tip_amount": "3.00",
            "notes": "reviewed",
        },
    ).json()
    assert (
        data["category"],
        data["original_amount"],
        data["claim_amount"],
        data["tip_amount"],
        data["split_count"],
    ) == ("DRINK", "20.01", "8.51", "3.00", 2)
    assert {"MEAL_SPLIT", "TIP_EXCLUDED"} <= set(data["flags"])
    assert data["notes"] == "reviewed" and data["is_user_verified"]
    data = client.patch(path, json={"receipt_doc_id": None}).json()
    assert data["payment_status"] == "missing_payment" and (
        "MISSING_PAYMENT" in data["flags"]
    )
    assert [doc["id"] for doc in data["documents"]] == [str(invoice)]
    data = client.patch(path, json={"receipt_doc_id": str(receipt)}).json()
    assert data["payment_status"] == "paired"
    _add(conn, user, batch, "image/jpeg", _ocr("Screenshot"))
    assert client.post(f"/api/v1/batches/{batch}/process").status_code == 200
    saved = conn.execute(
        "SELECT category,split_count,tip_amount,claim_amount::text,notes,"
        "is_user_verified,flags "
        "FROM public.transactions WHERE id=%s",
        (tx,),
    ).fetchone()
    links = conn.execute(
        "SELECT raw_document_id FROM public.transaction_documents "
        "WHERE transaction_id=%s "
        "ORDER BY raw_document_id",
        (tx,),
    ).fetchall()
    assert saved == ("DRINK", 2, 3, "8.51", "reviewed", True, data["flags"])
    assert {row[0] for row in links} == {invoice, receipt}


def test_patch_transaction_rejects_invalid_and_cross_tenant(process_api: tuple) -> None:
    client, conn, users, user, batch, tx, invoice, receipt = _processed_pair(
        process_api
    )
    path = f"/api/v1/transactions/{tx}"
    assert client.patch(path, json={}, headers={"Authorization": ""}).status_code == 401
    foreign_batch = _batch_for_user(conn, users[1], batch)
    foreign = _transaction_row(conn, users[1], foreign_batch)
    foreign_before = _transaction_state(conn, foreign)
    a, b = (
        client.patch(f"/api/v1/transactions/{i}", json={"notes": "x"})
        for i in (foreign, uuid4())
    )
    assert a.status_code == b.status_code == 404 and a.text == b.text
    assert _transaction_state(conn, foreign) == foreign_before
    pending, _ = _add(conn, user, batch, "image/jpeg")
    alternate_batch = _batch_for_user(conn, user, batch)
    wrong_batch, _ = _add(conn, user, alternate_batch, "image/jpeg")
    foreign_user, _ = _add(conn, users[1], batch, "image/jpeg")
    duplicate, _ = _add(conn, user, batch, "image/jpeg")
    missing = str(uuid4())
    busy, _ = _add(conn, user, batch, "image/jpeg")
    doc_ids = (pending, wrong_batch, foreign_user, duplicate, busy)
    params = (pending, busy, duplicate, invoice, *doc_ids)
    conn.execute(
        "UPDATE public.raw_documents SET "
        "ocr_status=CASE WHEN id=%s THEN ocr_status ELSE 'completed' END,"
        "doc_role=CASE WHEN id=%s THEN 'payment_receipt' ELSE 'invoice' END,"
        "duplicate_of_id=CASE WHEN id=%s THEN %s ELSE NULL END "
        "WHERE id IN (%s,%s,%s,%s,%s)",
        params,
    )
    other = _transaction_row(conn, user, batch)
    conn.execute(
        "INSERT INTO public.transaction_documents VALUES (%s,%s,%s,'receipt')",
        (other, busy, user),
    )
    conn.execute(
        "DELETE FROM public.transaction_documents WHERE raw_document_id=%s",
        (receipt,),
    )
    bad = [
        ({"extra": 1}, 422),
        ({}, 422),
        *[({"split_count": n}, 422) for n in (0, 100, None)],
        *[
            ({"tip_amount": n}, 422)
            for n in (-1, 30, None, "SYNTHETIC_PRIVATE_SENTINEL")
        ],
        *[({key: None}, 422) for key in ("category", "notes")],
        ({"category": "NOPE"}, 422),
        ({"invoice_doc_id": missing}, 422),
        ({"invoice_doc_id": str(receipt)}, 422),
        ({"invoice_doc_id": str(pending)}, 422),
        ({"invoice_doc_id": str(wrong_batch)}, 422),
        ({"invoice_doc_id": str(foreign_user)}, 422),
        ({"invoice_doc_id": str(duplicate)}, 422),
        ({"invoice_doc_id": str(invoice), "receipt_doc_id": str(invoice)}, 422),
        ({"invoice_doc_id": None, "receipt_doc_id": None}, 422),
        ({"receipt_doc_id": str(busy)}, 409),
    ]
    hidden = []
    hidden_ids = {
        missing,
        str(pending),
        str(wrong_batch),
        str(foreign_user),
        str(duplicate),
        str(receipt),
    }
    for body, code in bad:
        before = _transaction_state(conn, tx)
        response = client.patch(path, json=body)
        assert response.status_code == code and _transaction_state(conn, tx) == before
        if "SYNTHETIC_PRIVATE_SENTINEL" in response.text:
            pytest.fail("PATCH validation response exposed synthetic input")
        if body.get("invoice_doc_id") in hidden_ids:
            hidden.append(response.text)
    assert len(set(hidden)) == 1
