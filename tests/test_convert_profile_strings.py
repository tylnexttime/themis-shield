"""eval/convert_profile.py accepts profiles whose lists hold bare strings (other authors' profiles), not only the
object entries of harbourline.json. All values are invented."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))

from convert_profile import convert  # noqa: E402

from themis_shield.detectors.profile_adapter import adapt  # noqa: E402

PROFILE = {
    "organisation": "Lanternfield Water Co-operative",
    "organisation_forms": ["Lanternfield Water", "LWC"],
    "description": "An invented regional water co-operative.",
    "email_domain": "lanternfield.example",
    "sites": ["Ferncombe", "Ferncombe Treatment Plant"],
    "buildings": ["Pump House B"],
    "role_mailboxes": ["enquiries@lanternfield.example"],
    "main_lines": ["(03) 5550 1234"],
    "programmes": ["Leak Watch", "Smart Meter Rollout"],
    "research_projects": ["Aquifer Recharge Trial"],
    "public_dates": ["1 March 2027"],
}


def test_string_entries_convert():
    out = convert(PROFILE, about="test")
    assert out["campuses"][0] == {"town": "Ferncombe", "forms": ["Ferncombe"]}
    assert out["main_lines"] == [{"purpose": "main", "number": "(03) 5550 1234"}]
    assert out["programmes"][0] == {"code": "", "title": "Leak Watch"}
    assert out["projects"] == [{"code": "", "title": "Aquifer Recharge Trial"}]
    assert out["public_dates"] == [{"event": "1 March 2027", "date": "1 March 2027"}]


def test_string_sites_are_never_allow_listed():
    # A bare string cannot say whether it is a facility or a suburb people live in, so no string site is
    # allow-listed (conservative: some over-redaction, no leak).
    adapted = adapt(convert(PROFILE, about="test"))
    literals = adapted["literals"]
    assert "Ferncombe" not in literals and "Ferncombe Treatment Plant" not in literals
    assert "Leak Watch" in literals and " Leak Watch" not in literals and "" not in literals
    assert "(03) 5550 1234" in literals
    assert adapted["date_literals"] == ["1 March 2027"]
