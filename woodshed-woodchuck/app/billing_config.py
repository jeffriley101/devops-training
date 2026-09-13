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

    @classmethod
    def from_environment(cls):
        return cls(**{key: os.getenv(key.upper(), "false").strip().lower() in {"true", "1", "yes"}
                      for key in cls.__dataclass_fields__})

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
