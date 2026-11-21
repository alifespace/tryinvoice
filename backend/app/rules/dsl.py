import re
from collections.abc import Mapping
from string import Formatter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FILE_PLACEHOLDERS = {
    "category",
    "date",
    "currency",
    "amount_hyphen",
    "role_suffix",
    "ext",
    "direction",
    "iata",
}
_EXCEL_PLACEHOLDERS = {"expense_type_label", "batch_date"}
_CURRENCY_PATTERN = re.compile(r"(?:[A-Z]{3}|\*)\Z")


class _DSLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpenseType(_DSLModel):
    code: str
    label: str
    require_trip_reference: bool


class Category(_DSLModel):
    code: str
    label: str
    group: str


class Region(_DSLModel):
    code: str
    label: str
    currencies: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def currencies_are_valid(self) -> Self:
        if any(not _CURRENCY_PATTERN.fullmatch(code) for code in self.currencies):
            raise ValueError("币种必须为三位大写代码或 *。")
        return self


class RegionScope(_DSLModel):
    label: str
    regions: list[Region] = Field(min_length=1)


class RegionHierarchy(_DSLModel):
    in_scope: RegionScope
    out_of_scope: RegionScope
    preserve_original_currency: bool

    @model_validator(mode="after")
    def region_codes_are_unique(self) -> Self:
        codes = [
            region.code
            for scope in (self.in_scope, self.out_of_scope)
            for region in scope.regions
        ]
        if len(codes) != len(set(codes)):
            raise ValueError("地域代码必须在所有范围内唯一。")
        return self


class TipExclusion(_DSLModel):
    enabled: bool
    providers: list[str]
    highlight_flag: str

    @model_validator(mode="after")
    def enabled_exclusion_is_complete(self) -> Self:
        if self.enabled and (
            not self.providers
            or any(not provider.strip() for provider in self.providers)
            or not self.highlight_flag.strip()
        ):
            raise ValueError("启用小费剔除时必须提供供应商和标记。")
        return self


class ComplianceRules(_DSLModel):
    require_invoice_payment_closure: bool
    transit_direct_pay_exemptions: list[str]
    meal_split_for_multiple_diners: bool
    exclude_ride_hailing_tips: TipExclusion
    timezone_tolerance_days: int = Field(strict=True, ge=0, le=7)
    check_missing_airport_transfers: bool
    deduplicate_reshot_receipts: bool
    filter_irrelevant_files: bool


class NamingAndExport(_DSLModel):
    standard_pattern: str
    airport_transfer_pattern: str
    excel_group_levels: list[
        Literal["category_group", "region_scope", "region_detail_and_currency"]
    ] = Field(min_length=1)
    excel_filename_pattern: str

    @model_validator(mode="after")
    def patterns_and_groups_are_valid(self) -> Self:
        _validate_pattern(self.standard_pattern, _FILE_PLACEHOLDERS, {"ext"})
        _validate_pattern(
            self.airport_transfer_pattern,
            _FILE_PLACEHOLDERS,
            {"ext", "direction", "iata"},
        )
        _validate_pattern(self.excel_filename_pattern, _EXCEL_PLACEHOLDERS, set())
        if len(self.excel_group_levels) != len(set(self.excel_group_levels)):
            raise ValueError("Excel 分组层级不可重复。")
        return self


class RuleTemplateDSL(_DSLModel):
    schema_version: Literal["1.0"]
    template_code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    expense_types: list[ExpenseType] = Field(min_length=1)
    categories: list[Category] = Field(min_length=1)
    region_hierarchy: RegionHierarchy
    compliance_rules: ComplianceRules
    naming_and_export: NamingAndExport

    @model_validator(mode="after")
    def codes_are_unique(self) -> Self:
        expense_codes = [item.code for item in self.expense_types]
        category_codes = [item.code for item in self.categories]
        if len(expense_codes) != len(set(expense_codes)):
            raise ValueError("费用类型代码必须唯一。")
        if len(category_codes) != len(set(category_codes)):
            raise ValueError("分类代码必须唯一。")
        return self


def _validate_pattern(pattern: str, allowed: set[str], required: set[str]) -> None:
    try:
        fields: set[str] = set()
        for _, field_name, format_spec, conversion in Formatter().parse(pattern):
            if field_name is None:
                continue
            if field_name not in allowed:
                raise ValueError("命名模板包含未知占位符。")
            if format_spec or conversion is not None:
                raise ValueError("命名模板仅支持纯占位符。")
            fields.add(field_name)
    except ValueError as error:
        raise ValueError("命名模板格式无效。") from error
    if required - fields:
        raise ValueError("命名模板缺少必需占位符。")


def validate_rules(data: Mapping[str, object]) -> RuleTemplateDSL:
    return RuleTemplateDSL.model_validate(data)
