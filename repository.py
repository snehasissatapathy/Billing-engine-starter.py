"""
Repositories — the ONLY place SQL lives.

Each repository wraps the Database connection and exposes methods that
take/return domain dataclasses (defined in billing_engine/models/).

⚠️ YOU IMPLEMENT every method body marked TODO.
   The signatures, docstrings, and the LedgerRepository's append-only
   guarantee are already in place — do not change them.

Conventions:
  - Always use parameterized queries (`?` placeholders) — NEVER f-string SQL.
  - Money values are persisted as TEXT using `money.to_storage()`.
  - Dates are persisted as ISO strings (`date.isoformat()`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from billing_engine.db.database import Database
from billing_engine.money import Money
from billing_engine.models import (
    Customer,
    Plan, PricingType, BillingPeriod,
    Subscription, SubscriptionStatus,
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind,
    LedgerEntry, LedgerDirection,
)


# ============================================================
# CUSTOMERS
# ============================================================
class CustomerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, customer: Customer) -> Customer:
        """Insert and return the customer with `id` populated."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO customers (name, email, country_code, state_code) VALUES (?, ?, ?, ?)",
                (customer.name, customer.email, customer.country_code, customer.state_code or ""),
            )
            new_id = cur.lastrowid
        return Customer(
            id=new_id,
            name=customer.name,
            email=customer.email,
            country_code=customer.country_code,
            state_code=customer.state_code,
        )

    def get(self, customer_id: int) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            return None
        return Customer(id=row["id"], name=row["name"], email=row["email"],
                        country_code=row["country_code"], state_code=row["state_code"])

    def find_by_email(self, email: str) -> Optional[Customer]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE email = ?", (email,)).fetchone()
        if row is None:
            return None
        return Customer(id=row["id"], name=row["name"], email=row["email"],
                        country_code=row["country_code"], state_code=row["state_code"])

    def list_all(self) -> list[Customer]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM customers").fetchall()
        return [Customer(id=r["id"], name=r["name"], email=r["email"],
                         country_code=r["country_code"], state_code=r["state_code"]) for r in rows]


# ============================================================
# PLANS  +  PLAN TIERS
# ============================================================
class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan: Plan) -> Plan:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plans (name, pricing_type, billing_period, currency, config_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (plan.name, plan.pricing_type.value, plan.billing_period.value,
                 plan.currency, plan.config_json),
            )
            new_id = cur.lastrowid
        return Plan(id=new_id, name=plan.name, pricing_type=plan.pricing_type,
                    billing_period=plan.billing_period, currency=plan.currency,
                    config_json=plan.config_json)

    def get(self, plan_id: int) -> Optional[Plan]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            return None
        return Plan(id=row["id"], name=row["name"],
                    pricing_type=PricingType(row["pricing_type"]),
                    billing_period=BillingPeriod(row["billing_period"]),
                    currency=row["currency"], config_json=row["config_json"])

    def list_all(self) -> list[Plan]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM plans").fetchall()
        return [Plan(id=r["id"], name=r["name"],
                     pricing_type=PricingType(r["pricing_type"]),
                     billing_period=BillingPeriod(r["billing_period"]),
                     currency=r["currency"], config_json=r["config_json"]) for r in rows]


class PlanTierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan_id: int, from_units: int, to_units: Optional[int], unit_price: Money) -> int:
        """Insert a tier; return new id."""
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plan_tiers (plan_id, from_units, to_units, unit_price) VALUES (?, ?, ?, ?)",
                (plan_id, from_units, to_units, unit_price.to_storage()),
            )
            return cur.lastrowid

    def list_for_plan(self, plan_id: int, currency: str) -> list[tuple[int, Optional[int], Money]]:
        """Return [(from_units, to_units, unit_price)] ordered by from_units.

        Currency is passed in (the plan_tiers table stores only the amount;
        currency lives on the parent plan).
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT from_units, to_units, unit_price FROM plan_tiers WHERE plan_id = ? ORDER BY from_units",
                (plan_id,),
            ).fetchall()
        return [(r["from_units"], r["to_units"], Money(r["unit_price"], currency)) for r in rows]


# ============================================================
# DISCOUNTS
# ============================================================
class DiscountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, code: str, discount_type: str, value: str, currency: Optional[str] = None) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO discounts (code, discount_type, value, currency) VALUES (?, ?, ?, ?)",
                (code, discount_type, value, currency),
            )
            return cur.lastrowid

    def get_by_code(self, code: str) -> Optional[dict]:
        """Return raw row as dict, or None. (Discount has no dataclass yet — we use a dict for now.)"""
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM discounts WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        return dict(row)


# ============================================================
# SUBSCRIPTIONS
# =============================================================
class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription: Subscription) -> Subscription:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO subscriptions (customer_id, plan_id, status, current_period_start, "
                "current_period_end, trial_end, discount_id, past_due_since) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subscription.customer_id,
                    subscription.plan_id,
                    subscription.status.value,
                    subscription.current_period_start.isoformat(),
                    subscription.current_period_end.isoformat(),
                    subscription.trial_end.isoformat() if subscription.trial_end else None,
                    subscription.discount_id,
                    subscription.past_due_since.isoformat() if subscription.past_due_since else None,
                ),
            )
            new_id = cur.lastrowid
        return self._row_to_sub(dict(
            id=new_id,
            customer_id=subscription.customer_id,
            plan_id=subscription.plan_id,
            status=subscription.status.value,
            current_period_start=subscription.current_period_start.isoformat(),
            current_period_end=subscription.current_period_end.isoformat(),
            trial_end=subscription.trial_end.isoformat() if subscription.trial_end else None,
            discount_id=subscription.discount_id,
            past_due_since=subscription.past_due_since.isoformat() if subscription.past_due_since else None,
        ))

    def _row_to_sub(self, r) -> Subscription:
        return Subscription(
            id=r["id"],
            customer_id=r["customer_id"],
            plan_id=r["plan_id"],
            status=SubscriptionStatus(r["status"]),
            current_period_start=date.fromisoformat(r["current_period_start"]),
            current_period_end=date.fromisoformat(r["current_period_end"]),
            trial_end=date.fromisoformat(r["trial_end"]) if r["trial_end"] else None,
            discount_id=r["discount_id"],
            past_due_since=date.fromisoformat(r["past_due_since"]) if r["past_due_since"] else None,
        )

    def get(self, subscription_id: int) -> Optional[Subscription]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_sub(dict(row))

    def list_all(self) -> list[Subscription]:
        """All subscriptions, regardless of status. Used by BillingCycle trial scan."""
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM subscriptions").fetchall()
        return [self._row_to_sub(dict(r)) for r in rows]

    def get_due_for_billing(self, as_of: date) -> list[Subscription]:
        """Subscriptions whose current_period_end <= as_of AND status is ACTIVE.
        (Hint: trial subscriptions whose trial_end <= as_of should also become billable —
         either handle that here or transition them to ACTIVE first in BillingCycle.)
        """
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE status = 'ACTIVE' AND current_period_end <= ?",
                (as_of.isoformat(),),
            ).fetchall()
        return [self._row_to_sub(dict(r)) for r in rows]

    def update_period(self, subscription_id: int, new_start: date, new_end: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET current_period_start = ?, current_period_end = ? WHERE id = ?",
                (new_start.isoformat(), new_end.isoformat(), subscription_id),
            )

    def update_status(
        self,
        subscription_id: int,
        new_status: SubscriptionStatus,
        past_due_since: Optional[date] = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET status = ?, past_due_since = ? WHERE id = ?",
                (new_status.value,
                 past_due_since.isoformat() if past_due_since else None,
                 subscription_id),
            )

    def update_plan(self, subscription_id: int, new_plan_id: int) -> None:
        """Switch the subscription to a different plan (used by upgrade flow)."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                (new_plan_id, subscription_id),
            )


# ============================================================
# USAGE
# ============================================================
class UsageRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription_id: int, metric: str, quantity: int) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO usage_records (subscription_id, metric, quantity) VALUES (?, ?, ?)",
                (subscription_id, metric, quantity),
            )
            return cur.lastrowid

    def sum_for_period(
        self, subscription_id: int, metric: str, period_start: date, period_end: date
    ) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS qty FROM usage_records "
                "WHERE subscription_id = ? AND metric = ?",
                (subscription_id, metric),
            ).fetchone()
        return int(row["qty"])


# ============================================================
# INVOICES + LINE ITEMS
# ============================================================
class InvoiceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, invoice: Invoice) -> Invoice:
        """Insert invoice (NOT line items — that's the other repo).

        Must respect the UNIQUE(subscription_id, period_start) constraint.
        If a duplicate is attempted, raise sqlite3.IntegrityError naturally
        (caller is responsible for handling it — this gives idempotency).
        """
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO invoices (subscription_id, period_start, period_end, currency, "
                "subtotal, discount_total, tax_total, total, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invoice.subscription_id,
                    invoice.period_start.isoformat(),
                    invoice.period_end.isoformat(),
                    invoice.subtotal.currency,
                    invoice.subtotal.to_storage(),
                    invoice.discount_total.to_storage(),
                    invoice.tax_total.to_storage(),
                    invoice.total.to_storage(),
                    invoice.status.value,
                ),
            )
            new_id = cur.lastrowid
        invoice.id = new_id
        return invoice

    def get(self, invoice_id: int) -> Optional[Invoice]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            return None
        currency = row["currency"]
        return Invoice(
            id=row["id"],
            subscription_id=row["subscription_id"],
            period_start=date.fromisoformat(row["period_start"]),
            period_end=date.fromisoformat(row["period_end"]),
            subtotal=Money(row["subtotal"], currency),
            discount_total=Money(row["discount_total"], currency),
            tax_total=Money(row["tax_total"], currency),
            total=Money(row["total"], currency),
            status=InvoiceStatus(row["status"]),
        )

    def count_for_subscription(self, subscription_id: int) -> int:
        """Used by FirstMonthFree discount."""
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM invoices WHERE subscription_id = ?",
                (subscription_id,),
            ).fetchone()
        return int(row["cnt"])

    def mark_paid(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET status = 'PAID' WHERE id = ?", (invoice_id,))

    def mark_failed(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET status = 'FAILED' WHERE id = ?", (invoice_id,))

    def set_pdf_path(self, invoice_id: int, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE invoices SET pdf_path = ? WHERE id = ?", (path, invoice_id))


class InvoiceLineItemRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, line_item: InvoiceLineItem) -> InvoiceLineItem:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) "
                "VALUES (?, ?, ?, ?)",
                (line_item.invoice_id, line_item.description,
                 line_item.amount.to_storage(), line_item.kind.value),
            )
            new_id = cur.lastrowid
        return InvoiceLineItem(
            id=new_id,
            invoice_id=line_item.invoice_id,
            description=line_item.description,
            amount=line_item.amount,
            kind=line_item.kind,
        )

    def list_for_invoice(self, invoice_id: int) -> list[InvoiceLineItem]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,)
            ).fetchall()
        # We don't know currency here; it's embedded in the amount string via to_storage
        # But we need to know the currency — get it from the invoice
        if not rows:
            return []
        with self.db.connect() as conn:
            inv_row = conn.execute(
                "SELECT currency FROM invoices WHERE id = ?", (rows[0]["invoice_id"],)
            ).fetchone()
        currency = inv_row["currency"] if inv_row else "INR"
        return [
            InvoiceLineItem(
                id=r["id"],
                invoice_id=r["invoice_id"],
                description=r["description"],
                amount=Money(r["amount"], currency),
                kind=LineItemKind(r["kind"]),
            )
            for r in rows
        ]


# ============================================================
# LEDGER — APPEND-ONLY (do not implement update/delete)
# ============================================================
class LedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, entry: LedgerEntry) -> LedgerEntry:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO ledger_entries (invoice_id, customer_id, amount, currency, direction, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.invoice_id,
                    entry.customer_id,
                    entry.amount.to_storage(),
                    entry.amount.currency,
                    entry.direction.value,
                    entry.reason,
                ),
            )
            new_id = cur.lastrowid
        return LedgerEntry(
            id=new_id,
            invoice_id=entry.invoice_id,
            customer_id=entry.customer_id,
            amount=entry.amount,
            direction=entry.direction,
            reason=entry.reason,
        )

    def list_for_customer(self, customer_id: int) -> list[LedgerEntry]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE customer_id = ? ORDER BY created_at",
                (customer_id,),
            ).fetchall()
        return [
            LedgerEntry(
                id=r["id"],
                invoice_id=r["invoice_id"],
                customer_id=r["customer_id"],
                amount=Money(r["amount"], r["currency"]),
                direction=LedgerDirection(r["direction"]),
                reason=r["reason"],
            )
            for r in rows
        ]

    # ✅ These two methods are intentionally implemented to REJECT — do not override.
    def update(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")


# ============================================================
# PAYMENT ATTEMPTS
# ============================================================
class PaymentAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        invoice_id: int,
        attempt_no: int,
        status: str,
        failure_reason: Optional[str],
        next_retry_at: Optional[datetime],
    ) -> int:
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO payment_attempts (invoice_id, attempt_no, status, failure_reason, next_retry_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at.isoformat() if next_retry_at else None,
                ),
            )
            return cur.lastrowid

    def list_for_invoice(self, invoice_id: int) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_attempts WHERE invoice_id = ? ORDER BY attempt_no",
                (invoice_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_for_invoice(self, invoice_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM payment_attempts WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        return int(row["cnt"])