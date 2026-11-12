from fastapi import Request

from app.core.config import Settings
from app.services.description_service import DescriptionService
from app.services.listing_service import ListingService
from app.services.related_images_service import RelatedImagesService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_listing_service(request: Request) -> ListingService:
    return request.app.state.listing_service


def get_description_service(request: Request) -> DescriptionService:
    return request.app.state.description_service


def get_related_images_service(request: Request) -> RelatedImagesService:
    return request.app.state.related_images_service
