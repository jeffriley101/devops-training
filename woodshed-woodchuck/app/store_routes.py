from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_routes import current_profile
from .db import SessionLocal
from .store_catalog import catalog_payload
from .store_inventory import (
    InsufficientDandelionsError,
    StoreItemUnavailableError,
    list_owned_items,
    owned_item_payload,
    purchase_catalog_item,
)


router = APIRouter(prefix="/store", tags=["store"])


class StorePurchaseSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=50)


@router.get("/catalog")
def get_store_catalog():
    return catalog_payload()


@router.get("/inventory")
def get_store_inventory(request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        return {
            "items": [
                owned_item_payload(item)
                for item in list_owned_items(session, profile_id=profile.id)
            ]
        }


@router.post("/purchases", status_code=201)
def create_store_purchase(request: Request, submitted: StorePurchaseSubmission):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            owned, balance = purchase_catalog_item(
                session,
                profile_id=profile.id,
                item_key=submitted.item_key,
            )
            session.commit()
        except StoreItemUnavailableError as error:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(error)) from error
        except InsufficientDandelionsError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception:
            session.rollback()
            raise
        return {
            "item": owned_item_payload(owned),
            "dandelion_balance": balance,
        }
