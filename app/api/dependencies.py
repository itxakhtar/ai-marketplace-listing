from fastapi import Request

from app.core.config import Settings
from app.services.listing_service import ListingService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_listing_service(request: Request) -> ListingService:
    return request.app.state.listing_service
