import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).parents[2]
IOS = ROOT / "ios"


def _project() -> dict:
    return yaml.safe_load((IOS / "project.yml").read_text())


def test_project_declares_ios_deployment_target_and_app_target() -> None:
    project = _project()
    assert project["options"]["deploymentTarget"]["iOS"] == "17.0"
    assert project["options"]["bundleIdPrefix"] == "com.tryinvoice"
    assert project["settings"]["base"]["SWIFT_VERSION"] == "5.0"
    assert "5.10" not in (IOS / "project.yml").read_text()
    assert project["configs"] == {"Debug": "debug", "Release": "release"}

    app = project["targets"]["TryInvoice"]
    assert app["type"] == "application"
    assert app["platform"] == "iOS"
    assert app["sources"] == [{"path": "TryInvoice"}]
    assert app["configFiles"] == {
        "Debug": "Config/Debug.xcconfig",
        "Release": "Config/Release.xcconfig",
    }


def test_project_declares_shared_scheme_and_generated_info_properties() -> None:
    project = _project()
    app = project["targets"]["TryInvoice"]
    assert app["info"]["path"] == "build/Info.plist"
    assert Path(app["info"]["path"]).parts[0] == "build"
    assert app["info"]["properties"] == {
        "API_BASE_URL": "$(API_BASE_URL)",
        "SUPABASE_URL": "$(SUPABASE_URL)",
        "SUPABASE_ANON_KEY": "$(SUPABASE_ANON_KEY)",
        "UILaunchScreen": {},
    }
    scheme = project["schemes"]["TryInvoice"]
    assert scheme["build"]["targets"]["TryInvoice"] == "all"
    assert scheme["test"]["config"] == "Debug"
    assert scheme["test"]["targets"] == ["TryInvoiceTests", "TryInvoiceUITests"]
    assert scheme["management"]["shared"] is True


def test_project_declares_unit_and_ui_test_targets() -> None:
    project = _project()
    targets = project["targets"]
    assert targets["TryInvoiceTests"]["type"] == "bundle.unit-test"
    assert targets["TryInvoiceUITests"]["type"] == "bundle.ui-testing"
    bundle_ids = set()
    for name, source_dir in (
        ("TryInvoiceTests", "TryInvoiceTests"),
        ("TryInvoiceUITests", "TryInvoiceUITests"),
    ):
        target = targets[name]
        assert target["platform"] == "iOS"
        assert target["dependencies"] == [{"target": "TryInvoice"}]
        assert target["sources"] == [{"path": source_dir}]
        assert target["settings"]["base"]["GENERATE_INFOPLIST_FILE"] == "YES"
        bundle_ids.add(target["settings"]["base"]["PRODUCT_BUNDLE_IDENTIFIER"])
        swift_sources = list((IOS / source_dir).glob("*.swift"))
        assert swift_sources
    assert len(bundle_ids) == 2


def test_project_disables_signing_without_team_or_profile_values() -> None:
    project = (IOS / "project.yml").read_text()
    assert "CODE_SIGNING_ALLOWED: NO" in project
    assert not re.search(
        r"(?im)^\s*(?:DEVELOPMENT_TEAM|PROVISIONING_PROFILE(?:_SPECIFIER)?|teamId):",
        project,
    )


def test_configurations_use_only_local_debug_and_empty_release_urls() -> None:
    debug = (IOS / "Config/Debug.xcconfig").read_text()
    release = (IOS / "Config/Release.xcconfig").read_text()
    assert re.search(r"(?m)^API_BASE_URL\s*=\s*http:/\$\(\)/localhost:8000$", debug)
    assert re.search(r"(?m)^API_BASE_URL\s*=\s*$", release)
    for contents in (debug, release):
        assert "https://" not in contents
        assert "eyJ" not in contents
        assert "sk-" not in contents
        assert "supabase.co" not in contents


def test_swift_sources_exist_under_the_declared_sources_directory() -> None:
    expected = (
        "App/TryInvoiceApp.swift",
        "App/RootView.swift",
        "Core/AppConfiguration.swift",
        "Core/AppError.swift",
        "Core/AppEnvironment.swift",
        "UI/ErrorBanner.swift",
    )
    for relative_path in expected:
        source = IOS / "TryInvoice" / relative_path
        assert source.is_file()
        assert source.resolve().is_relative_to((IOS / "TryInvoice").resolve())


def test_core_configuration_files_are_foundation_only() -> None:
    for name in ("AppConfiguration.swift", "AppError.swift", "AppEnvironment.swift"):
        source = (IOS / "TryInvoice/Core" / name).read_text()
        assert re.search(r"(?m)^import Foundation$", source)
        assert not re.search(r"(?m)^import (?:SwiftUI|UIKit)$", source)


def test_swift_sources_do_not_embed_service_urls() -> None:
    for source in (IOS / "TryInvoice").rglob("*.swift"):
        assert not re.search(r"https?://", source.read_text())


def test_readme_documents_current_project_and_manual_ci_gate() -> None:
    readme = (IOS / "README.md").read_text()
    for stale_phrase in ("尚无", "首次 push 待授权", "安全跳过"):
        assert stale_phrase not in readme
    assert "44915742124" in readme
    assert "人工门禁" in readme
    assert "TryInvoiceTests" in readme
    assert "TryInvoiceUITests" in readme
    assert "未授权" in readme


def test_swift_tests_use_only_synthetic_nonsecret_urls() -> None:
    test_sources = [
        *(IOS / "TryInvoiceTests").rglob("*.swift"),
        *(IOS / "TryInvoiceUITests").rglob("*.swift"),
    ]
    assert test_sources
    allowed_hosts = {"example.com", "localhost", "127.0.0.1"}
    url_pattern = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>)]+", re.IGNORECASE)
    for source in test_sources:
        contents = source.read_text()
        assert not re.search(r"eyJ|sk-|supabase\.co", contents)
        for match in url_pattern.finditer(contents):
            host = urlsplit(match.group()).hostname
            assert (
                host is None
                or host in allowed_hosts
                or host == "example.test"
                or host.endswith(".example.test")
            ), f"Unexpected host in {source.name}: {host}"


def test_generated_xcode_project_is_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text()
    assert "/ios/TryInvoice.xcodeproj/" in gitignore
    assert "/ios/build/" in gitignore
