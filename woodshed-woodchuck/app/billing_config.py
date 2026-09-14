"""Local billing policy. Enabling flags alone never enables an unbuilt adapter."""
from dataclasses import dataclass
import os
from types import MappingProxyType


@dataclass(frozen=True)
class BillingConfig:
    public_subscriptions_enabled: bool = False
    paypal_billing_enabled: bool = False
    stripe_billing_enabled: bool = False
    launch_sale_enabled: bool = False
    checkout_validity_seconds: int | None = None

    @classmethod
    def from_environment(cls):
        flags = {key: os.getenv(key.upper(), "false").strip().lower() in {"true", "1", "yes"}
                 for key in cls.__dataclass_fields__ if key != "checkout_validity_seconds"}
        value = os.getenv("CHECKOUT_VALIDITY_SECONDS", "")
        # No product hold duration has been selected. Missing/invalid configuration
        # fails closed at checkout; tests may explicitly supply a bounded value.
        validity = int(value) if value.isascii() and value.isdigit() and len(value) < 10 else None
        return cls(**flags, checkout_validity_seconds=validity if validity and validity > 0 else None)

    def provider_enabled(self, provider):
        return {"paypal": self.paypal_billing_enabled, "stripe": self.stripe_billing_enabled}.get(provider, False)


@dataclass(frozen=True)
class Plan:
    code: str
    label: str
    amount_cents: int
    interval: str
    currency: str = "USD"


PLANS = MappingProxyType({p.code: p for p in (
    Plan("full_monthly_7", "Full Monthly — $7/month", 700, "month"),
    Plan("full_annual_49", "Full Annual — $49/year", 4900, "year"),
    Plan("friendship_annual_30", "Launch Sale — $30/year", 3000, "year"),
)})


def available_plans(config):
    return [p for p in PLANS.values() if p.code != "friendship_annual_30" or config.launch_sale_enabled]


def new_subscription_plan(code, config):
    plan = PLANS.get(code)
    if plan is None or plan not in available_plans(config):
        raise ValueError("This plan is not available for a new subscription.")
    return plan
