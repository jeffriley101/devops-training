from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .account_routes import current_profile
from .db import SessionLocal
from .store_catalog import catalog_payload
from .store_inventory import (
    DecorationCollisionError,
    InsufficientDandelionsError,
    OwnedItemAccessError,
    StoreItemUnavailableError,
    claim_weekly_mum_snack,
    list_inventory_payloads,
    owned_item_payload,
    place_inventory_item,
    purchase_catalog_item,
    remove_inventory_item_placement,
    update_inventory_item_size,
)


router = APIRouter(prefix="/store", tags=["store"])


class StorePurchaseSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_key: str = Field(min_length=1, max_length=50)


class StorePlacementSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    size: Literal["medium", "large", "xlarge"] = "medium"


class StoreSizeSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: Literal["medium", "large", "xlarge"]


class MumSnackSubmission(BaseModel):
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
        return {"items": list_inventory_payloads(session, profile_id=profile.id)}


@router.put("/inventory/{inventory_id}/placement")
def update_store_item_placement(
    inventory_id: str,
    submitted: StorePlacementSubmission,
    request: Request,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            item = place_inventory_item(
                session,
                profile_id=profile.id,
                inventory_id=inventory_id,
                placement_x=submitted.x,
                placement_y=submitted.y,
                placement_size=submitted.size,
            )
            session.commit()
        except OwnedItemAccessError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except DecorationCollisionError as error:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception:
            session.rollback()
            raise
        return {"item": item}


@router.delete("/inventory/{inventory_id}/placement")
def delete_store_item_placement(inventory_id: str, request: Request):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            item = remove_inventory_item_placement(
                session,
                profile_id=profile.id,
                inventory_id=inventory_id,
            )
            session.commit()
        except OwnedItemAccessError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception:
            session.rollback()
            raise
        return {"item": item}


@router.put("/inventory/{inventory_id}/size")
def update_store_item_size(
    inventory_id: str,
    submitted: StoreSizeSubmission,
    request: Request,
):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            item = update_inventory_item_size(
                session,
                profile_id=profile.id,
                inventory_id=inventory_id,
                placement_size=submitted.size,
            )
            session.commit()
        except OwnedItemAccessError as error:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception:
            session.rollback()
            raise
        return {"item": item}


@router.post("/mum/snacks")
def claim_mum_snack(request: Request, submitted: MumSnackSubmission):
    with SessionLocal() as session:
        profile = current_profile(request, session)
        if profile is None:
            raise HTTPException(status_code=401, detail="Student sign-in is required.")
        try:
            item, created, week_start = claim_weekly_mum_snack(
                session,
                profile_id=profile.id,
                item_key=submitted.item_key,
            )
            session.commit()
        except ValueError as error:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception:
            session.rollback()
            raise
        return {
            "item": owned_item_payload(item),
            "created": created,
            "week_start": week_start.isoformat(),
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
