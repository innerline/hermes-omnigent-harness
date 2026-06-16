#!/usr/bin/env python3
"""
Register the Hermes harness with an installed Omnigent.

This script adds ``"hermes"`` to Omnigent's ``_HARNESS_MODULES`` registry
so you can use ``--harness hermes`` in agent specs.

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


_HARNESS_ENTRY = '    "hermes": "hermes_omnigent_harness.hermes_harness",'


def find_omnigent_init() -> Path:
    """Locate the Omnigent harnesses __init__.py.

    :returns: Path to ``omnigent/runtime/harnesses/__init__.py``.
    :raises FileNotFoundError: If Omnigent isn't installed.
    """
    # Try uv tool dir
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
    candidates.extend(
        Path("/usr/local/lib").glob("python*/site-packages/omnigent")
    )
    candidates.extend(
        Path.home().glob(".local/lib/python*/site-packages/omnigent")
    )

    for base in candidates:
        init_path = base / "runtime" / "harnesses" / "__init__.py"
        if init_path.exists():
            return init_path

    raise FileNotFoundError(
        "Could not find omnigent/runtime/harnesses/__init__.py. "
        "Ensure Omnigent is installed (pip install omnigent or uv tool install omnigent). "
        "Use --omnigent-path to specify the location manually."
    )


def register(init_path: Path) -> bool:
    """Add the Hermes harness entry to the registry.

    :param init_path: Path to the ``__init__.py`` file.
    :returns: True if the entry was added, False if it already existed.
    :raises ValueError: If the file structure doesn't match expectations.
    """
    content = init_path.read_text()

    # Check if already registered
    if '"hermes"' in content:
        print(f"✓ 'hermes' already registered in {init_path}")
        return False

    # Find the last entry in _HARNESS_MODULES and add after it
    # Look for the closing ``}`` of the dict
    pattern = r'(_HARNESS_MODULES:\s*dict\[str,\s*str\]\s*=\s*\{.*?)(\n\})'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        raise ValueError(
            f"Could not find _HARNESS_MODULES dict in {init_path}. "
            "The file structure may have changed in a newer Omnigent version."
        )

    new_content = content[:match.end(1)] + "\n" + _HARNESS_ENTRY + content[match.end(1):]

    init_path.write_text(new_content)
    print(f"✓ Added 'hermes' harness to {init_path}")
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
        init_path = args.omnigent_path / "runtime" / "harnesses" / "__init__.py"
        if not init_path.exists():
            print(f"Error: {init_path} not found", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            init_path = find_omnigent_init()
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Omnigent harnesses __init__.py: {init_path}")

    try:
        added = register(init_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if added:
        print()
        print("Registration complete! You can now use:")
        print("  omni run my-agent/ --harness hermes")
    else:
        print("No changes needed.")


if __name__ == "__main__":
    main()
