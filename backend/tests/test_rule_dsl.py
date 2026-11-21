import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.rules.dsl import RuleTemplateDSL, validate_rules
from app.rules.seed import load_cmi_default


def test_cmi_default_covers_required_rules() -> None:
    template = load_cmi_default()
    assert template.template_code == "cmi_default_v1"
    assert [item.code for item in template.categories] == [
        "EAT",
        "DRINK",
        "TRANS",
        "Others",
        "UNKNOWN",
    ]
    hierarchy = template.region_hierarchy
    assert [(item.code, item.currencies) for item in hierarchy.in_scope.regions] == [
        ("americas", ["USD"])
    ]
    assert {item.code: item.currencies for item in hierarchy.out_of_scope.regions} == {
        "mainland_cn": ["CNY"],
        "hk": ["HKD"],
        "sg": ["SGD"],
        "other_overseas": ["*"],
    }
    compliance = template.compliance_rules
    assert compliance.exclude_ride_hailing_tips.enabled
    assert compliance.exclude_ride_hailing_tips.highlight_flag == "TIP_EXCLUDED"
    assert compliance.meal_split_for_multiple_diners
    assert compliance.require_invoice_payment_closure
    assert compliance.check_missing_airport_transfers
    assert template.naming_and_export.standard_pattern == (
        "{category}_{date}_{currency}_{amount_hyphen}{role_suffix}.{ext}"
    )
    assert template.naming_and_export.airport_transfer_pattern == (
        "TRANS_{direction}{iata}_{date}_{currency}_{amount_hyphen}{role_suffix}.{ext}"
    )
    assert template.naming_and_export.excel_group_levels == [
        "category_group",
        "region_scope",
        "region_detail_and_currency",
    ]


def _set_value(data: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("unexpected",), True),
        (("categories", 1, "code"), "EAT"),
        (("region_hierarchy", "out_of_scope", "regions", 0, "code"), "americas"),
        (("expense_types",), []),
        (("region_hierarchy", "in_scope", "regions", 0, "currencies"), ["usd"]),
        (("region_hierarchy", "in_scope", "regions", 0, "currencies"), ["US"]),
        (("compliance_rules", "timezone_tolerance_days"), 8),
        (("compliance_rules", "exclude_ride_hailing_tips", "providers"), []),
        (
            ("compliance_rules", "exclude_ride_hailing_tips", "highlight_flag"),
            " ",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{unknown}_{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category:^10}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category:５}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category:{ext}}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category!r}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category.x}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{}.{ext}",
        ),
        (
            ("naming_and_export", "standard_pattern"),
            "{category}_{date}_{currency}_{amount_hyphen}{role_suffix}",
        ),
        (
            ("naming_and_export", "airport_transfer_pattern"),
            "TRANS_{direction}_{date}.{ext}",
        ),
        (("naming_and_export", "standard_pattern"), "{"),
        (
            ("naming_and_export", "excel_group_levels"),
            ["unknown"],
        ),
        (
            ("naming_and_export", "excel_group_levels"),
            ["region_scope", "region_scope"],
        ),
        (("schema_version",), "2.0"),
        (("template_code",), "Not-A-Code"),
    ],
)
def test_invalid_rules_are_rejected(path: tuple[str | int, ...], value: Any) -> None:
    data = copy.deepcopy(load_cmi_default().model_dump(mode="json"))
    _set_value(data, path, value)
    with pytest.raises(ValidationError):
        validate_rules(data)


def test_literal_braces_are_accepted() -> None:
    data = copy.deepcopy(load_cmi_default().model_dump(mode="json"))
    data["naming_and_export"]["standard_pattern"] = "{category}{{x}}.{ext}"

    assert validate_rules(data).naming_and_export.standard_pattern == (
        "{category}{{x}}.{ext}"
    )


def test_rule_template_round_trip() -> None:
    template = load_cmi_default()
    assert validate_rules(template.model_dump(mode="json")) == template
    assert isinstance(template, RuleTemplateDSL)


def test_all_nested_models_forbid_unknown_fields() -> None:
    data = load_cmi_default().model_dump(mode="json")
    data["categories"][0]["extra"] = "not allowed"
    with pytest.raises(ValidationError):
        validate_rules(data)
