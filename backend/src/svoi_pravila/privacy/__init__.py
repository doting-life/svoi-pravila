"""Shared privacy copy (export / revoke / delete) for bot and mini-app API."""

from svoi_pravila.privacy.catalog import PrivacyCatalog, load_privacy_catalog

__all__ = ["PrivacyCatalog", "load_privacy_catalog"]
