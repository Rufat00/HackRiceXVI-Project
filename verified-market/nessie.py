"""
Capital One Nessie client.

Real mode hits http://api.nessieisreal.com (key as ?key= query param).
Mock mode keeps an in-memory ledger with the same shape so the app runs
identically without a key. Toggle with NESSIE_MOCK=1.
"""
import os
import uuid
import time
import requests

BASE = "http://api.nessieisreal.com"
KEY = os.environ.get("NESSIE_KEY", "")
MOCK = os.environ.get("NESSIE_MOCK", "1" if not KEY else "0") == "1"


class NessieError(Exception):
    pass


# ----------------------------------------------------------------- mock ledger
# Persisted to a JSON file so restarting the server mid-demo doesn't lose money.
import json
_LEDGER_PATH = os.environ.get("MOCK_LEDGER_PATH", "mock_ledger.json")
try:
    with open(_LEDGER_PATH) as _f:
        _state = json.load(_f)
except Exception:
    _state = {"accounts": {}, "transfers": []}
_mock_accounts = _state["accounts"]   # account_id -> {"_id", "nickname", "balance", "customer_id"}
_mock_transfers = _state["transfers"]


def _save():
    with open(_LEDGER_PATH, "w") as f:
        json.dump({"accounts": _mock_accounts, "transfers": _mock_transfers}, f)


def _mock_create_account(customer_id, nickname, balance):
    acct_id = "mock_" + uuid.uuid4().hex[:12]
    _mock_accounts[acct_id] = {
        "_id": acct_id, "type": "Checking", "nickname": nickname,
        "balance": balance, "customer_id": customer_id,
    }
    _save()
    return _mock_accounts[acct_id]


def _mock_transfer(src, dst, amount, description):
    if src not in _mock_accounts or dst not in _mock_accounts:
        raise NessieError("Unknown account")
    if _mock_accounts[src]["balance"] < amount:
        raise NessieError("Insufficient funds")
    _mock_accounts[src]["balance"] -= amount
    _mock_accounts[dst]["balance"] += amount
    tx = {
        "_id": "tx_" + uuid.uuid4().hex[:10], "payer_id": src, "payee_id": dst,
        "amount": amount, "description": description, "status": "executed",
        "transaction_date": time.strftime("%Y-%m-%d"),
    }
    _mock_transfers.append(tx)
    _save()
    return tx


# --------------------------------------------------------------- real client
def _req(method, path, **kw):
    r = requests.request(method, f"{BASE}{path}", params={"key": KEY},
                         timeout=15, **kw)
    if r.status_code >= 400:
        raise NessieError(f"Nessie {r.status_code}: {r.text[:200]}")
    data = r.json()
    # Nessie wraps POST results as {"code":201,"message":..,"objectCreated":{...}}
    return data.get("objectCreated", data)


# ------------------------------------------------------------------ public API
def create_customer(first_name, last_name):
    """Returns customer_id. Nessie needs a full address; we use a campus one."""
    if MOCK:
        return "cust_" + uuid.uuid4().hex[:10]
    body = {
        "first_name": first_name, "last_name": last_name or "Student",
        "address": {"street_number": "6100", "street_name": "Main St",
                    "city": "Houston", "state": "TX", "zip": "77005"},
    }
    return _req("POST", "/customers", json=body)["_id"]


def create_account(customer_id, nickname, balance=0):
    """Returns account dict with _id and balance."""
    if MOCK:
        return _mock_create_account(customer_id, nickname, balance)
    body = {"type": "Checking", "nickname": nickname,
            "rewards": 0, "balance": balance}
    return _req("POST", f"/customers/{customer_id}/accounts", json=body)


def get_account(account_id):
    if MOCK:
        if account_id not in _mock_accounts:
            raise NessieError("Unknown account")
        return _mock_accounts[account_id]
    return _req("GET", f"/accounts/{account_id}")


def transfer(src_account, dst_account, amount, description):
    """Move money between two accounts. Returns the transfer record."""
    if MOCK:
        return _mock_transfer(src_account, dst_account, amount, description)
    body = {"medium": "balance", "payee_id": dst_account, "amount": amount,
            "transaction_date": time.strftime("%Y-%m-%d"),
            "description": description}
    return _req("POST", f"/accounts/{src_account}/transfers", json=body)


def mode():
    return "mock" if MOCK else "live"
