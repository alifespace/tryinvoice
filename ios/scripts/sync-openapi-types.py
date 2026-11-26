#!/usr/bin/env python3
"""Generate the small Swift API model subset consumed by the iOS client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "contracts/openapi.json"
OUTPUT_PATH = ROOT / "ios/TryInvoice/Core/API/APITypes.generated.swift"
SCHEMA_NAMES = (
    "BatchCreate",
    "BatchOut",
    "BatchPage",
    "Category",
    "CategoryGroup",
    "ComplianceRules",
    "ConfirmRequest",
    "DocumentOut",
    "ExpenseType",
    "HTTPValidationError",
    "NamingAndExport",
    "PresignRequest",
    "PresignResponse",
    "Region",
    "RegionGroup",
    "RegionHierarchy",
    "RegionScope",
    "RuleTemplateDSL",
    "ScopeGroup",
    "TemplateClone",
    "TemplateCreate",
    "TemplateOut",
    "TemplatePatch",
    "TipExclusion",
    "TransactionDocumentOut",
    "TransactionListOut",
    "TransactionOut",
    "TransactionPatch",
    "ValidationError",
)

JSON_VALUE = """indirect enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case integer(Int)
    case number(Double)
    case boolean(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() {
            self = .null
        } else if let decoded = try? value.decode(Bool.self) {
            self = .boolean(decoded)
        } else if let decoded = try? value.decode(Int.self) {
            self = .integer(decoded)
        } else if let decoded = try? value.decode(Double.self) {
            self = .number(decoded)
        } else if let decoded = try? value.decode(String.self) {
            self = .string(decoded)
        } else if let decoded = try? value.decode([String: JSONValue].self) {
            self = .object(decoded)
        } else if let decoded = try? value.decode([JSONValue].self) {
            self = .array(decoded)
        } else {
            throw DecodingError.dataCorruptedError(
                in: value,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case let .string(decoded):
            try value.encode(decoded)
        case let .integer(decoded):
            try value.encode(decoded)
        case let .number(decoded):
            try value.encode(decoded)
        case let .boolean(decoded):
            try value.encode(decoded)
        case let .object(decoded):
            try value.encode(decoded)
        case let .array(decoded):
            try value.encode(decoded)
        case .null:
            try value.encodeNil()
        }
    }
}"""


def _camel_case(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _swift_type(schema: dict[str, Any]) -> tuple[str, bool]:
    variants = schema.get("anyOf")
    if variants:
        return _union_type(variants, schema)

    reference = schema.get("$ref")
    if reference:
        return reference.rsplit("/", 1)[-1], False

    schema_type = schema.get("type")
    if schema_type == "array":
        return _array_type(schema)
    if schema_type == "object":
        return _object_type(schema)

    primitive_types = {
        "boolean": "Bool",
        "integer": "Int",
        "number": "Double",
        "string": "String",
    }
    return _primitive_type(schema, primitive_types)


def _union_type(
    variants: list[dict[str, Any]], schema: dict[str, Any]
) -> tuple[str, bool]:
    non_null = [variant for variant in variants if variant.get("type") != "null"]
    nullable = len(non_null) != len(variants)
    if len(non_null) == 1:
        swift_type, _ = _swift_type(non_null[0])
        return swift_type, nullable
    simple_types = {"boolean", "integer", "number", "string"}
    if all(variant.get("type") in simple_types for variant in non_null):
        return "JSONValue", nullable
    # Free-form JSON payloads (object/array, optionally null) stay opaque.
    if all(
        variant.get("type") in {"object", "array"} for variant in non_null
    ) and len(non_null) > 1:
        return "JSONValue", nullable
    raise ValueError(f"Unsupported anyOf schema: {schema!r}")


def _array_type(schema: dict[str, Any]) -> tuple[str, bool]:
    item = schema.get("items", {})
    item_type = "JSONValue" if not item else _swift_type(item)[0]
    return f"[{item_type}]", False


def _object_type(schema: dict[str, Any]) -> tuple[str, bool]:
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict) and additional.get("type") == "string":
        return "[String: String]", False
    return "JSONValue", False


def _primitive_type(
    schema: dict[str, Any], primitive_types: dict[str, str]
) -> tuple[str, bool]:
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in primitive_types:
        raise ValueError(f"Unsupported OpenAPI schema: {schema!r}")
    return primitive_types[schema_type], False


def _render_struct(name: str, schema: dict[str, Any]) -> str:
    properties = _properties_for_schema(name, schema)
    required = set(schema.get("required", []))
    members = _members_for_properties(properties, required)
    lines = [f"struct {name}: Codable, Equatable, Sendable {{"]
    lines.extend(
        f"    let {swift_name}: {swift_type}" for _, swift_name, swift_type in members
    )
    if any(key != swift_name for key, swift_name, _ in members):
        lines.extend(_coding_keys(members))
    lines.append("}")
    return "\n".join(lines)


def _properties_for_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = schema.get("properties", {})
    if name != "ValidationError":
        return properties
    # Validation payloads can contain user input; retain only safe structure.
    return {key: properties[key] for key in ("loc", "msg", "type") if key in properties}


def _members_for_properties(
    properties: dict[str, Any], required: set[str]
) -> list[tuple[str, str, str]]:
    members: list[tuple[str, str, str]] = []
    for key in sorted(properties):
        swift_name = _camel_case(key)
        swift_type, nullable = _swift_type(properties[key])
        optional = nullable or key not in required
        members.append((key, swift_name, swift_type + ("?" if optional else "")))
    return members


def _coding_keys(members: list[tuple[str, str, str]]) -> list[str]:
    lines = ["", "    enum CodingKeys: String, CodingKey {"]
    for key, swift_name, _ in members:
        if key == swift_name:
            lines.append(f"        case {swift_name}")
        else:
            lines.append(f'        case {swift_name} = "{key}"')
    lines.append("    }")
    return lines


def render_types(contract: dict[str, Any]) -> str:
    schemas = contract["components"]["schemas"]
    missing = [name for name in SCHEMA_NAMES if name not in schemas]
    if missing:
        raise ValueError(f"Missing required OpenAPI schemas: {', '.join(missing)}")
    sections = [
        "// Generated by ios/scripts/sync-openapi-types.py. Do not edit.",
        "import Foundation",
        JSON_VALUE,
        *(_render_struct(name, schemas[name]) for name in SCHEMA_NAMES),
    ]
    return "\n\n".join(sections) + "\n"


def main(
    argv: list[str] | None = None,
    *,
    contract_path: Path = CONTRACT_PATH,
    output_path: Path = OUTPUT_PATH,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated content without writing the output file",
    )
    args = parser.parse_args(argv)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    generated = render_types(contract)

    if args.check:
        if (
            not output_path.is_file()
            or output_path.read_text(encoding="utf-8") != generated
        ):
            print(
                f"Generated API types are out of date: {output_path}", file=sys.stderr
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
