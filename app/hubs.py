"""The hub registry, validated on load.

`data/hubs.json` is hand-authored configuration and is therefore an ingress
boundary like any other: it passes through a Pydantic model before anything
touches it, and past that point the rest of the code operates on validated
types without re-checking.

The validation that matters most here is the FIPS keys: 5-character strings
whose leading zero must survive. A FIPS silently parsed as an integer becomes
8031 instead of "08031" and then matches nothing in either FEMA NRI or the
NWS alert feed -- for exactly the eleven states whose codes begin with zero.

There are two such keys, `nri_fips` and `nws_county_fips`, because FEMA and
NWS disagree about Connecticut: FEMA has moved to the post-2022 planning
regions and NWS has not. See the comment block in data/hubs.json.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import load_config

Region = Literal["Midwest", "Northeast", "South", "West"]


class Hub(BaseModel):
    """One distribution hub. Frozen: the registry is read-only at runtime."""

    model_config = {"frozen": True}

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: str = Field(min_length=2, max_length=2)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    # FEMA NRI's STCOFIPS. Post-2022 planning region codes in Connecticut.
    nri_fips: str
    # The legacy county FIPS the NWS alert feed still issues against.
    nws_county_fips: str
    county_name: str = Field(min_length=1)
    region: Region

    @field_validator("nri_fips", "nws_county_fips")
    @classmethod
    def _five_digit_fips(cls, value: str) -> str:
        if len(value) != 5 or not value.isdigit():
            raise ValueError(
                f"a FIPS key must be exactly 5 digits as a string, got {value!r} -- "
                "a leading zero stripped by integer parsing will match no county"
            )
        return value

    @property
    def keys_diverge(self) -> bool:
        """True where FEMA and NWS disagree about this hub's county. Surfaced
        in the response's assumptions so the reader knows the baseline and the
        live alerts were joined on different geographies."""
        return self.nri_fips != self.nws_county_fips

    @property
    def label(self) -> str:
        """How a hub is named to a human: 'Portland, OR' -- two hubs in this
        registry are called Portland."""
        return f"{self.name}, {self.state}"


class HubRegistry(BaseModel):
    model_config = {"frozen": True}

    hubs: tuple[Hub, ...]

    def by_id(self, hub_id: str) -> Hub | None:
        return next((h for h in self.hubs if h.id == hub_id), None)

    def by_nws_fips(self, county_fips: str) -> tuple[Hub, ...]:
        """Hubs in the county an NWS alert names. More than one hub may share a
        county in principle, so this returns all matches rather than the first."""
        return tuple(h for h in self.hubs if h.nws_county_fips == county_fips)

    @property
    def nri_fips_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(h.nri_fips for h in self.hubs))


def _load(path: Path) -> HubRegistry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    registry = HubRegistry(hubs=tuple(Hub(**h) for h in raw["hubs"]))

    ids = [h.id for h in registry.hubs]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate hub ids in {path}: {sorted(duplicates)}")
    return registry


@lru_cache(maxsize=1)
def load_hubs(path: Path | None = None) -> HubRegistry:
    """The registry, read once. Cached because it is immutable configuration."""
    return _load(path or load_config().hubs_path)
