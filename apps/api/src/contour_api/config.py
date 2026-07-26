"""Application configuration.

Every optional capability in Contour is gated by an explicit setting. When a
setting is absent the capability reports a :class:`DisabledCapabilityError` naming
what is missing and how to supply it. Nothing falls back to a default value,
a cached guess, or fabricated output — see docs/architecture.md, "Disabled
states", and rule §2.6 (unknown must remain unknown).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class ElevationProviderName(StrEnum):
    VALHALLA = "valhalla"
    RASTER = "raster"
    NONE = "none"


class GeocodingProviderName(StrEnum):
    PELIAS = "pelias"
    NOMINATIM_SELFHOSTED = "nominatim-selfhosted"
    NONE = "none"


class IntentParserName(StrEnum):
    DETERMINISTIC = "deterministic"
    ANTHROPIC = "anthropic"


class DisabledCapabilityError(Exception):
    """Raised when a capability is used without the configuration it requires.

    Carries the exact remedy so the API can return it to the user rather than a
    generic failure (§16.7). This is an explicit limited state, never a
    substitute result (§21.8).
    """

    def __init__(self, capability: str, reason: str, remedy: str) -> None:
        self.capability = capability
        self.reason = reason
        self.remedy = remedy
        super().__init__(f"{capability} is not available: {reason}. {remedy}")

    def as_dict(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "reason": self.reason,
            "remedy": self.remedy,
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CONTOUR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Core ---------------------------------------------------------------
    env: Literal["development", "test", "staging", "production"] = "development"
    database_url: PostgresDsn = Field(
        default="postgresql+psycopg://contour:contour@127.0.0.1:5432/contour"  # type: ignore[arg-type]
    )
    redis_url: RedisDsn = Field(default="redis://127.0.0.1:6379/0")  # type: ignore[arg-type]
    api_base_url: str = "http://127.0.0.1:8000"
    # Placeholder, not a credential: the app refuses to start in production
    # with this value. See docs/security.md.
    secret_key: str = "change-me-in-every-environment"  # noqa: S105
    token_encryption_key: str | None = None

    # -- Routing ------------------------------------------------------------
    valhalla_url: str | None = None
    valhalla_tile_dir: str = "/opt/contour/valhalla-tiles"
    valhalla_config: str = "/opt/contour/valhalla.json"
    # Bound on the solve -> validate -> re-solve loop (docs/route_validation.md).
    # Reached without a compliant result, the API returns the closest route and
    # names the violating segments rather than silently dropping the constraint.
    routing_max_resolve_attempts: int = 4

    # -- Elevation ----------------------------------------------------------
    elevation_provider: ElevationProviderName = ElevationProviderName.NONE
    elevation_raster_dir: str = "/opt/contour/dem"
    elevation_dataset_id: str | None = None

    # -- Geocoding ----------------------------------------------------------
    geocoding_provider: GeocodingProviderName = GeocodingProviderName.NONE
    geocoding_url: str | None = None

    # -- Sources ------------------------------------------------------------
    osm_extract_url: str | None = None
    overpass_url: str | None = None
    ckan_base_url: str | None = None
    source_archive_dir: str = "/opt/contour/source-archive"

    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # -- Intent parsing -----------------------------------------------------
    intent_parser: IntentParserName = IntentParserName.DETERMINISTIC
    anthropic_model: str = "claude-sonnet-5"

    # -- Observability ------------------------------------------------------
    log_level: str = "info"
    log_format: Literal["json", "console"] = "json"
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: str | None = None

    # -- Derived checks -----------------------------------------------------
    def require_valhalla(self) -> str:
        if not self.valhalla_url:
            raise DisabledCapabilityError(
                capability="routing",
                reason="no Valhalla endpoint is configured",
                remedy=(
                    "Build Valhalla with infra/valhalla/build.sh, start "
                    "valhalla_service, and set CONTOUR_VALHALLA_URL."
                ),
            )
        return self.valhalla_url

    def require_elevation(self) -> ElevationProviderName:
        if self.elevation_provider is ElevationProviderName.NONE:
            raise DisabledCapabilityError(
                capability="elevation",
                reason="no elevation provider is configured",
                remedy=(
                    "Set CONTOUR_ELEVATION_PROVIDER to 'valhalla' (requires "
                    "elevation tiles) or 'raster' (requires a DEM in "
                    "CONTOUR_ELEVATION_RASTER_DIR), and record the dataset in "
                    "CONTOUR_ELEVATION_DATASET_ID."
                ),
            )
        if (
            self.elevation_provider is ElevationProviderName.RASTER
            and not self.elevation_dataset_id
        ):
            # Provenance is not optional: an elevation figure without a named
            # source cannot be attributed or its accuracy stated (§11.2).
            raise DisabledCapabilityError(
                capability="elevation",
                reason="the raster provider has no CONTOUR_ELEVATION_DATASET_ID",
                remedy=(
                    "Set CONTOUR_ELEVATION_DATASET_ID to the exact DEM in use, "
                    "e.g. 'Copernicus DEM GLO-30 (2021 release)'."
                ),
            )
        return self.elevation_provider

    def require_geocoding(self) -> str:
        if self.geocoding_provider is GeocodingProviderName.NONE or not self.geocoding_url:
            raise DisabledCapabilityError(
                capability="geocoding",
                reason="no geocoding provider is configured",
                remedy=(
                    "Set CONTOUR_GEOCODING_PROVIDER and CONTOUR_GEOCODING_URL to "
                    "a self-hosted Pelias or Nominatim instance. Public Nominatim "
                    "is not permitted as an application backend by its usage policy."
                ),
            )
        return self.geocoding_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
