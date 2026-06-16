#!/usr/bin/env python3
"""
Register the Hermes harness with an installed Omnigent.

Patches three locations in the Omnigent installation:
  1. ``_HARNESS_MODULES`` — runtime registry (runtime/harnesses/__init__.py)
  2. ``OMNIGENT_HARNESSES`` — CLI allowlist (spec/_omnigent_compat.py)
  3. ``_OS_ENV_HARNESSES`` — OS env tool injection (cli.py)

Usage:
    python scripts/register_harness.py [--omnigent-path /path/to/omnigent]

Without --omnigent-path, it auto-detects the Omnigent installation via
``uv tool dir``.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


def find_omnigent_pkg() -> Path:
    """Locate the Omnigent package directory.

    :returns: Path to the ``omnigent`` package (containing __init__.py).
    :raises FileNotFoundError: If Omnigent isn't installed.
    """
    try:
        result = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True,
            text=True,
            check=True,
        )
        tool_dir = result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        tool_dir = str(Path.home() / ".local" / "share" / "uv" / "tools")

    candidates = list(Path(tool_dir).glob("omnigent/lib/python*/site-packages/omnigent"))
    candidates.extend(Path("/usr/local/lib").glob("python*/site-packages/omnigent"))
    candidates.extend(Path.home().glob(".local/lib/python*/site-packages/omnigent"))

    for base in candidates:
        if (base / "__init__.py").exists():
            return base

    raise FileNotFoundError(
        "Could not find the omnigent package. "
        "Ensure Omnigent is installed (pip install omnigent or uv tool install omnigent). "
        "Use --omnigent-path to specify the location manually."
    )


def patch_harness_modules(pkg_path: Path) -> bool:
    """Add 'hermes' to _HARNESS_MODULES in runtime/harnesses/__init__.py.

    :returns: True if added, False if already present.
    """
    init_path = pkg_path / "runtime" / "harnesses" / "__init__.py"
    content = init_path.read_text()

    if '"hermes"' in content:
        print("  ✓ 'hermes' already in _HARNESS_MODULES")
        return False

    entry = '    "hermes": "hermes_omnigent_harness.hermes_harness",'
    pattern = r'(_HARNESS_MODULES:\s*dict\[str,\s*str\]\s*=\s*\{.*?)(\n\})'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        raise ValueError(f"Could not find _HARNESS_MODULES dict in {init_path}")

    new_content = content[:match.end(1)] + "\n" + entry + content[match.end(1):]
    init_path.write_text(new_content)
    print("  ✓ Added 'hermes' to _HARNESS_MODULES")
    return True


def patch_omnigent_harnesses(pkg_path: Path) -> bool:
    """Add 'hermes' to OMNIGENT_HARNESSES frozenset.

    :returns: True if added, False if already present.
    """
    compat_path = pkg_path / "spec" / "_omnigent_compat.py"
    content = compat_path.read_text()

    # Check if already present in the frozenset
    if re.search(r'"hermes"', content.split("OMNIGENT_HARNESS_ALIASES")[0]):
        print("  ✓ 'hermes' already in OMNIGENT_HARNESSES")
        return False

    old = '        "pi",\n    },\n)'
    new = '        "pi",\n        "hermes",\n    },\n)'

    if old not in content:
        raise ValueError(f"Could not find insertion point in {compat_path}")

    content = content.replace(old, new, 1)
    compat_path.write_text(content)
    print("  ✓ Added 'hermes' to OMNIGENT_HARNESSES")
    return True


def patch_os_env_harnesses(pkg_path: Path) -> bool:
    """Add 'hermes' to _OS_ENV_HARNESSES in cli.py.

    :returns: True if added, False if already present.
    """
    cli_path = pkg_path / "cli.py"
    content = cli_path.read_text()

    if '"hermes"' in content.split("_OS_ENV_HARNESSES")[1].split("\n")[0]:
        print("  ✓ 'hermes' already in _OS_ENV_HARNESSES")
        return False

    old = '_OS_ENV_HARNESSES: frozenset[str] = frozenset({"claude-sdk", "codex", "pi"})'
    new = '_OS_ENV_HARNESSES: frozenset[str] = frozenset({"claude-sdk", "codex", "pi", "hermes"})'

    if old not in content:
        print("  ⚠ Could not find _OS_ENV_HARNESSES pattern (may have changed)")
        return False

    content = content.replace(old, new, 1)
    cli_path.write_text(content)
    print("  ✓ Added 'hermes' to _OS_ENV_HARNESSES")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register the Hermes harness with Omnigent."
    )
    parser.add_argument(
        "--omnigent-path",
        type=Path,
        help="Path to the omnigent package directory (auto-detected by default).",
    )
    args = parser.parse_args()

    if args.omnigent_path:
        pkg_path = args.omnigent_path
        if not (pkg_path / "__init__.py").exists():
            print(f"Error: {pkg_path} is not a valid omnigent package", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            pkg_path = find_omnigent_pkg()
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Omnigent package: {pkg_path}")
    print()

    changed = False
    try:
        print("1. Runtime registry (_HARNESS_MODULES):")
        changed |= patch_harness_modules(pkg_path)
        print()
        print("2. CLI allowlist (OMNIGENT_HARNESSES):")
        changed |= patch_omnigent_harnesses(pkg_path)
        print()
        print("3. OS env injection (_OS_ENV_HARNESSES):")
        changed |= patch_os_env_harnesses(pkg_path)
    except ValueError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    if changed:
        print("Registration complete! You can now use:")
        print("  omni run my-agent/ --harness hermes")
    else:
        print("All patches already applied — no changes needed.")


if __name__ == "__main__":
    main()
