"""Marketplace taxonomies. Single source of truth for validation AND prompts."""

from enum import Enum


class Category(str, Enum):
    ELECTRONICS = "Electronics"
    MOBILES_AND_TABLETS = "Mobiles and Tablets"
    FASHION = "Fashion"
    VEHICLES = "Vehicles"
    HOME_AND_LIVING = "Home and Living"
    TOOLS_AND_INDUSTRIAL = "Tools and Industrial"


class Condition(str, Enum):
    BRAND_NEW = "Brand New"
    LIKE_NEW = "Like New"
    USED_GOOD = "Used - Good Condition"
    USED_FAIR = "Used - Fair Condition"
    POOR = "Poor Condition"
    UNABLE_TO_DETERMINE = "Unable to Determine"
