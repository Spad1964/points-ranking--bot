from services.ranks import (
    QUALIFICATION_ROLE_IDS,
    get_rank_by_points_and_qualifications,
    should_request_promotion,
)

MEDICAL = QUALIFICATION_ROLE_IDS["Medical"]
MARKSMAN = QUALIFICATION_ROLE_IDS["Marksman"]
INVENTIVE = QUALIFICATION_ROLE_IDS["Inventive"]
MELEE = QUALIFICATION_ROLE_IDS["Melee"]
PILOT = QUALIFICATION_ROLE_IDS["Pilot"]


def test_unqualified_rank_falls_back() -> None:
    assert get_rank_by_points_and_qualifications(1500, set())["rank"] == "D"


def test_all_qualifications_required() -> None:
    assert get_rank_by_points_and_qualifications(700, {MEDICAL, MARKSMAN})["rank"] == "D"
    assert get_rank_by_points_and_qualifications(700, {MEDICAL, MARKSMAN, INVENTIVE})["rank"] == "C"


def test_fallback_to_lower_qualified_rank() -> None:
    assert get_rank_by_points_and_qualifications(1500, {PILOT})["rank"] == "B"


def test_promotion_only_when_above_current_rank() -> None:
    assert should_request_promotion("B", 1500, {PILOT}) is None
    assert should_request_promotion("B", 1500, {MELEE})["rank"] == "A"


if __name__ == "__main__":
    test_unqualified_rank_falls_back()
    test_all_qualifications_required()
    test_fallback_to_lower_qualified_rank()
    test_promotion_only_when_above_current_rank()
    print("ranks ok")
