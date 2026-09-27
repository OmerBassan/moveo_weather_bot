"""Tests for the two failure modes that would be invisible in production.

Both of these are silent-wrong-answer bugs rather than crashes, which is why
they are pinned here: each one would surface to a user as "this hub is fine",
and neither would raise anything.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import same_to_fips
from app.hubs import Hub, load_hubs


class TestSameToFips:
    """NWS SAME codes are `0` + the 5-digit FIPS, so the conversion is a slice.

    A `lstrip("0")` implementation passes the Texas case and fails every state
    whose FIPS begins with zero -- and the symptom is "no alerts in the West",
    which reads as low risk rather than as a bug.
    """

    def test_leading_zero_state_is_preserved(self) -> None:
        # Howard County, Arkansas: the example in the NWS geolocation guide.
        assert same_to_fips("005133") == "05133"

    def test_denver_keeps_its_leading_zero(self) -> None:
        assert same_to_fips("008031") == "08031"

    def test_non_zero_state_is_unaffected(self) -> None:
        assert same_to_fips("048201") == "48201"  # Harris County, Texas

    @pytest.mark.parametrize("bad", ["8031", "0080311", "", "08031x"])
    def test_malformed_codes_are_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="6 digits"):
            same_to_fips(bad)


class TestHubValidation:
    def test_fips_parsed_as_integer_is_rejected(self) -> None:
        """The registry is JSON, and JSON has no string type discipline. A FIPS
        that arrives as 8031 instead of "08031" matches no county in either
        upstream, so it must fail at load rather than at lookup."""
        with pytest.raises(ValidationError):
            Hub(
                id="x", name="X", state="CO", latitude=39.7, longitude=-104.9,
                nri_fips="8031", nws_county_fips="08031",
                county_name="Denver", region="West",
            )


class TestRegistryContents:
    """Properties of data/hubs.json itself."""

    def test_forty_hubs_ten_per_region(self) -> None:
        registry = load_hubs()
        assert len(registry.hubs) == 40
        counts: dict[str, int] = {}
        for hub in registry.hubs:
            counts[hub.region] = counts.get(hub.region, 0) + 1
        # Even regional coverage is what makes "which Midwest hubs are most
        # exposed?" rank a meaningful field instead of three items.
        assert counts == {"Midwest": 10, "Northeast": 10, "South": 10, "West": 10}

    def test_hartford_is_the_known_fema_nws_divergence(self) -> None:
        """Connecticut abolished its counties for statistical purposes in 2022.
        FEMA NRI v1.20.0 followed the Census Bureau onto planning regions;
        the NWS alert feed did not. One join key would be silently wrong for
        one of the two upstreams.

        If this test ever fails because NWS has caught up, the fix is to
        update the registry -- not to delete the second key.
        """
        registry = load_hubs()
        hartford = registry.by_id("hartford-ct")
        assert hartford is not None
        assert hartford.nri_fips == "09110"  # Capitol Planning Region
        assert hartford.nws_county_fips == "09003"  # legacy Hartford County
        assert hartford.keys_diverge

    def test_hartford_is_the_only_divergence(self) -> None:
        registry = load_hubs()
        diverging = [h.id for h in registry.hubs if h.keys_diverge]
        assert diverging == ["hartford-ct"]

    def test_two_hubs_named_portland_are_distinguishable(self) -> None:
        """Portland, ME and Portland, OR are both in the registry, which is why
        `label` carries the state -- an agent that resolves "Portland" to a
        single hub is guessing."""
        registry = load_hubs()
        portlands = sorted(h.label for h in registry.hubs if h.name == "Portland")
        assert portlands == ["Portland, ME", "Portland, OR"]
