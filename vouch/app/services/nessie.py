"""Capital One Nessie client.

Nessie is a mock-banking API: customers own accounts, accounts hold balances
(in dollars), and transfers move money between accounts. We use it as the
escrow rail: each transaction gets its own escrow account owned by the
platform customer, so money is visibly held by neither buyer nor seller.

Set NESSIE_API_KEY to talk to the real sandbox. With no key we run a local
ledger that mimics the same request/response shapes, so the UI and state
machine behave identically either way.

Docs: http://api.nessieisreal.com/documentation
"""
import json
import os
import threading
import uuid
from datetime import date

import requests


class NessieError(Exception):
    pass


# --------------------------------------------------------------------------
# Real client
# --------------------------------------------------------------------------
class NessieClient:
    def __init__(self, api_key, base_url):
        self.key = api_key
        self.base = base_url.rstrip("/")
        self.mock = False

    def _req(self, method, path, body=None):
        url = f"{self.base}{path}"
        try:
            r = requests.request(method, url, params={"key": self.key}, json=body, timeout=15)
        except requests.RequestException as e:
            raise NessieError(f"Nessie unreachable: {e}") from e
        if r.status_code >= 400:
            raise NessieError(f"Nessie {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    @staticmethod
    def _created(resp):
        # Nessie wraps creates as {"code":201,"message":...,"objectCreated":{...}}
        obj = resp.get("objectCreated") or resp
        return obj.get("_id") or obj.get("id")

    def create_customer(self, first_name, last_name, address=None):
        address = address or {
            "street_number": "6100", "street_name": "Main St",
            "city": "Houston", "state": "TX", "zip": "77005",
        }
        resp = self._req("POST", "/customers", {
            "first_name": first_name[:40] or "Vouch",
            "last_name": last_name[:40] or "User",
            "address": address,
        })
        return self._created(resp)

    def create_account(self, customer_id, nickname, balance_cents=0, acct_type="Checking"):
        resp = self._req("POST", f"/customers/{customer_id}/accounts", {
            "type": acct_type,
            "nickname": nickname[:60],
            "rewards": 0,
            "balance": round(balance_cents / 100, 2),
        })
        return self._created(resp)

    def get_account(self, account_id):
        acct = self._req("GET", f"/accounts/{account_id}")
        return {
            "id": acct.get("_id"),
            "nickname": acct.get("nickname"),
            "type": acct.get("type"),
            "balance_cents": int(round(float(acct.get("balance", 0)) * 100)),
            "customer_id": acct.get("customer_id"),
        }

    def deposit(self, account_id, amount_cents, description="deposit"):
        resp = self._req("POST", f"/accounts/{account_id}/deposits", {
            "medium": "balance",
            "transaction_date": date.today().isoformat(),
            "amount": round(amount_cents / 100, 2),
            "description": description[:100],
        })
        return self._created(resp)

    def transfer(self, from_account_id, to_account_id, amount_cents, description=""):
        resp = self._req("POST", f"/accounts/{from_account_id}/transfers", {
            "medium": "balance",
            "payee_id": to_account_id,
            "amount": round(amount_cents / 100, 2),
            "transaction_date": date.today().isoformat(),
            "description": description[:100],
        })
        return self._created(resp)

    def list_transfers(self, account_id):
        return self._req("GET", f"/accounts/{account_id}/transfers")


# --------------------------------------------------------------------------
# Mock client: same interface, local JSON ledger
# --------------------------------------------------------------------------
class MockNessieClient:
    """Mirrors NessieClient exactly. Persists to a JSON file beside the DB so
    balances survive a server restart during the demo."""

    _lock = threading.Lock()

    def __init__(self, store_path):
        self.mock = True
        self.path = store_path
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.state = json.load(f)
        else:
            self.state = {"customers": {}, "accounts": {}, "transfers": []}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _id(prefix):
        return f"{prefix}_{uuid.uuid4().hex[:20]}"

    def create_customer(self, first_name, last_name, address=None):
        with self._lock:
            cid = self._id("cust")
            self.state["customers"][cid] = {"_id": cid, "first_name": first_name, "last_name": last_name}
            self._save()
            return cid

    def create_account(self, customer_id, nickname, balance_cents=0, acct_type="Checking"):
        with self._lock:
            if customer_id not in self.state["customers"]:
                raise NessieError("unknown customer")
            aid = self._id("acct")
            self.state["accounts"][aid] = {
                "_id": aid, "customer_id": customer_id, "nickname": nickname,
                "type": acct_type, "balance_cents": int(balance_cents),
            }
            self._save()
            return aid

    def get_account(self, account_id):
        a = self.state["accounts"].get(account_id)
        if not a:
            raise NessieError("unknown account")
        return {
            "id": a["_id"], "nickname": a["nickname"], "type": a["type"],
            "balance_cents": a["balance_cents"], "customer_id": a["customer_id"],
        }

    def deposit(self, account_id, amount_cents, description="deposit"):
        with self._lock:
            a = self.state["accounts"].get(account_id)
            if not a:
                raise NessieError("unknown account")
            a["balance_cents"] += int(amount_cents)
            tid = self._id("dep")
            self.state["transfers"].append({
                "_id": tid, "type": "deposit", "payee_id": account_id,
                "amount_cents": int(amount_cents), "description": description,
                "transaction_date": date.today().isoformat(),
            })
            self._save()
            return tid

    def transfer(self, from_account_id, to_account_id, amount_cents, description=""):
        with self._lock:
            src = self.state["accounts"].get(from_account_id)
            dst = self.state["accounts"].get(to_account_id)
            if not src or not dst:
                raise NessieError("unknown account")
            if src["balance_cents"] < amount_cents:
                raise NessieError("insufficient funds")
            src["balance_cents"] -= int(amount_cents)
            dst["balance_cents"] += int(amount_cents)
            tid = self._id("xfer")
            self.state["transfers"].append({
                "_id": tid, "type": "transfer", "payer_id": from_account_id,
                "payee_id": to_account_id, "amount_cents": int(amount_cents),
                "description": description, "transaction_date": date.today().isoformat(),
            })
            self._save()
            return tid

    def list_transfers(self, account_id):
        return [t for t in self.state["transfers"]
                if t.get("payer_id") == account_id or t.get("payee_id") == account_id]


def build_client(config):
    if config["NESSIE_MOCK"]:
        store = os.path.join(os.path.dirname(config["DATABASE_PATH"]), "nessie_mock_ledger.json")
        return MockNessieClient(store)
    return NessieClient(config["NESSIE_API_KEY"], config["NESSIE_BASE_URL"])
