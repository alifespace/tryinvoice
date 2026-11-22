import os
import stat
import sys
from pathlib import Path
from urllib.parse import unquote_to_bytes

DIAGNOSTICS_DIR = Path(__file__).absolute().parents[2] / "ios" / "build" / "ci"
SECRET_ENV_NAMES = tuple(
    f"TEST_RUNNER_TRYINVOICE_SEAM_{name}"
    for name in "API_BASE_URL SUPABASE_URL SUPABASE_ANON_KEY EMAIL PASSWORD".split()
)


def _contains_secret(contents: bytes, secrets: list[bytes]) -> bool:
    decoded = unquote_to_bytes(contents)
    form_decoded = unquote_to_bytes(contents.replace(b"+", b" "))
    return any(
        secret in value
        for secret in secrets
        for value in (contents, decoded, form_decoded)
    )


def _mark_scan_complete() -> None:
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as file:
            file.write("scan_complete=true\n")


def _exclude_symlink_directories(
    root: str, dirnames: list[str], errors: list[Path]
) -> None:
    for dirname in dirnames[:]:
        path = Path(root) / dirname
        try:
            if stat.S_ISLNK(path.lstat().st_mode):
                errors.append(path)
                dirnames.remove(dirname)
        except OSError:
            errors.append(path)


def _unsafe_directory(anchor: Path, directory: Path) -> bool | None:
    try:
        current = anchor.absolute()
        for part in ("", *directory.absolute().relative_to(current).parts):
            current /= part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                return True
    except FileNotFoundError:
        return None
    except OSError:
        return True
    return False


def _scan_files(directory: Path, secrets: list[bytes]) -> tuple[list[Path], list[Path]]:
    matches: list[Path] = []
    errors: list[Path] = []
    path_state = _unsafe_directory(directory.parents[2], directory)
    if path_state is True:
        return matches, [directory]
    if path_state is None:
        return matches, errors

    for root, dirnames, filenames in os.walk(
        directory, onerror=lambda _: errors.append(directory)
    ):
        _exclude_symlink_directories(root, dirnames, errors)
        for filename in filenames:
            path = Path(root) / filename
            try:
                relative = path.relative_to(directory).as_posix().encode()
                if stat.S_ISREG(path.stat().st_mode) and (
                    _contains_secret(relative, secrets)
                    or _contains_secret(path.read_bytes(), secrets)
                ):
                    matches.append(path)
            except OSError:
                errors.append(path)
    return matches, errors


def main() -> int:
    secrets = [
        value.encode() for name in SECRET_ENV_NAMES if (value := os.environ.get(name))
    ]
    if not secrets:
        print("skip: no configured test secrets")
        _mark_scan_complete()
        return 0

    matches, errors = _scan_files(DIAGNOSTICS_DIR, secrets)
    for path in matches:
        try:
            path.unlink()
        except OSError:
            errors.append(path)
    for path in sorted(set(matches + errors)):
        relative = path.relative_to(DIAGNOSTICS_DIR).as_posix()
        label = relative or "."
        if _contains_secret(relative.encode(), secrets):
            label = "[redacted path]"
        print(label)
    if errors:
        return 2
    _mark_scan_complete()
    return int(bool(matches))


if __name__ == "__main__":
    sys.exit(main())
