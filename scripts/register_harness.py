#!/usr/bin/env python3
"""
Register the Hermes harness with an installed Omnigent.

Patches six locations in the Omnigent installation:
  1. ``_HARNESS_MODULES`` — runtime registry (runtime/harnesses/__init__.py)
  2. ``OMNIGENT_HARNESSES`` — CLI allowlist (spec/_omnigent_compat.py)
  3. ``_OS_ENV_HARNESSES`` — OS env tool injection (cli.py)
  4. ``_HARNESS_MODEL_ENV_KEY`` — model override mapping (runner/app.py)
  5. ``_build_spawn_env_from_spec`` — Hermes dispatch case (runner/app.py)
  6. ``_build_hermes_spawn_env`` — credential/model bridge (runtime/workflow.py)

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

    candidates = list(
        Path(tool_dir).glob("omnigent/lib/python*/site-packages/omnigent")
    )
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
    pattern = r"(_HARNESS_MODULES:\s*dict\[str,\s*str\]\s*=\s*\{.*?)(\n\})"
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        raise ValueError(f"Could not find _HARNESS_MODULES dict in {init_path}")

    new_content = content[: match.end(1)] + "\n" + entry + content[match.end(1) :]
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


def patch_model_env_key(pkg_path: Path) -> bool:
    """Add 'hermes' to _HARNESS_MODEL_ENV_KEY in runner/app.py.

    :returns: True if added, False if already present.
    """
    app_path = pkg_path / "runner" / "app.py"
    content = app_path.read_text()

    section = content.split("_HARNESS_MODEL_ENV_KEY")[1].split("\n\n")[0]
    if '"hermes"' in section:
        print("  ✓ 'hermes' already in _HARNESS_MODEL_ENV_KEY")
        return False

    old = '    "openai-agents": "HARNESS_OPENAI_AGENTS_MODEL",\n}'
    new = '    "openai-agents": "HARNESS_OPENAI_AGENTS_MODEL",\n    "hermes": "HARNESS_HERMES_MODEL",\n}'

    if old not in content:
        print("  ⚠ Could not find _HARNESS_MODEL_ENV_KEY pattern")
        return False

    content = content.replace(old, new, 1)
    app_path.write_text(content)
    print("  ✓ Added 'hermes' to _HARNESS_MODEL_ENV_KEY")
    return True


def patch_spawn_env_dispatch(pkg_path: Path) -> bool:
    """Add Hermes case + import to _build_spawn_env_from_spec in runner/app.py.

    :returns: True if added, False if already present.
    """
    app_path = pkg_path / "runner" / "app.py"
    content = app_path.read_text()

    needs_case = "_build_hermes_spawn_env(spec" not in content
    needs_import = "_build_hermes_spawn_env," not in content

    if not needs_case and not needs_import:
        print("  ✓ Dispatch case already patched")
        return False

    if needs_case:
        old = '        elif harness == "openai-agents":\n            env = _build_openai_agents_sdk_spawn_env(spec)\n        else:'
        new = '        elif harness == "openai-agents":\n            env = _build_openai_agents_sdk_spawn_env(spec)\n        elif harness == "hermes":\n            env = _build_hermes_spawn_env(spec, workdir=workdir)\n        else:'
        if old in content:
            content = content.replace(old, new, 1)
        else:
            print("  ⚠ Could not find dispatch pattern")
            return False

    if needs_import:
        old_imp = "            _build_pi_spawn_env,\n        )"
        new_imp = "            _build_pi_spawn_env,\n            _build_hermes_spawn_env,\n        )"
        if old_imp in content:
            content = content.replace(old_imp, new_imp, 1)

    app_path.write_text(content)
    print("  ✓ Added Hermes dispatch + import to runner/app.py")
    return True


def patch_workflow_builder(pkg_path: Path) -> bool:
    """Add _build_hermes_spawn_env function to runtime/workflow.py.

    :returns: True if added, False if already present.
    """
    wf_path = pkg_path / "runtime" / "workflow.py"
    content = wf_path.read_text()

    if "def _build_hermes_spawn_env" in content:
        print("  ✓ _build_hermes_spawn_env already exists")
        return False

    marker = '    os_env_payload = _serialize_os_env(spec.os_env)\n    if os_env_payload is not None:\n        env["HARNESS_PI_OS_ENV"] = os_env_payload\n    return env\n'

    if marker not in content:
        print("  ⚠ Could not find insertion point in workflow.py")
        return False

    hermes_func = '''

def _build_hermes_spawn_env(
    spec: AgentSpec,
    *,
    workdir: Path | None = None,
) -> dict[str, str]:
    """
    Build the env-var dict the Hermes harness wrap reads.

    Hermes manages its own credentials (~/.hermes/config.yaml + .env),
    so unlike the other harnesses we only pass the model and optional
    overrides. Hermes resolves API keys and gateway URLs from its own
    config at runtime.
    """
    env: dict[str, str] = {}
    model = _resolve_spec_model(spec)
    if model is not None:
        env["HARNESS_HERMES_MODEL"] = model

    base_url = spec.executor.config.get("base_url")
    if base_url:
        env["HARNESS_HERMES_BASE_URL"] = str(base_url)

    api_key = spec.executor.config.get("api_key")
    if api_key:
        env["HARNESS_HERMES_API_KEY"] = str(api_key)

    profile = spec.executor.config.get("profile")
    if profile:
        env["HARNESS_HERMES_PROFILE"] = str(profile)

    toolsets = spec.executor.config.get("enabled_toolsets")
    if toolsets:
        env["HARNESS_HERMES_ENABLED_TOOLSETS"] = str(toolsets)

    disabled = spec.executor.config.get("disabled_toolsets")
    if disabled:
        env["HARNESS_HERMES_DISABLED_TOOLSETS"] = str(disabled)

    os_env_payload = _serialize_os_env(spec.os_env)
    if os_env_payload is not None:
        env["HARNESS_HERMES_OS_ENV"] = os_env_payload

    return env
'''

    content = content.replace(marker, marker + hermes_func, 1)
    wf_path.write_text(content)
    print("  ✓ Added _build_hermes_spawn_env to workflow.py")
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
        print()
        print("4. Model override mapping (_HARNESS_MODEL_ENV_KEY):")
        changed |= patch_model_env_key(pkg_path)
        print()
        print("5. Spawn-env dispatch (runner/app.py):")
        changed |= patch_spawn_env_dispatch(pkg_path)
        print()
        print("6. Credential bridge (_build_hermes_spawn_env):")
        changed |= patch_workflow_builder(pkg_path)
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
