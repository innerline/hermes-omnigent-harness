"""
Auto-registration mechanism for the Hermes Omnigent harness.

This module provides:
1. ``main()`` — CLI entry point (``hermes-register``) that patches Omnigent
2. ``_register_at_startup()`` — called automatically via the ``.pth`` file
   so the harness is registered when Python starts, without manual patching.

The ``.pth`` approach: Python executes lines starting with ``import `` in
``*.pth`` files at interpreter startup. Our ``_hermes_omnigent_register.pth``
calls ``_register_at_startup()`` which patches the Omnigent installation
in-place (idempotently) if Omnigent is importable.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path


def _find_omnigent_pkg() -> Path | None:
    """Locate the installed omnigent package directory.

    :returns: Path to the omnigent package, or None if not installed.
    """
    try:
        mod = importlib.import_module("omnigent")
        if mod.__file__:
            return Path(mod.__file__).parent
    except ImportError:
        pass

    # Fallback: search common uv tool locations
    tool_dirs = [
        Path.home() / ".local" / "share" / "uv" / "tools",
        Path("/usr/local/lib"),
    ]
    for base in tool_dirs:
        for pkg in base.glob("**/site-packages/omnigent/__init__.py"):
            return pkg.parent

    return None


def _patch_file(path: Path, check: str, find: str, replace: str) -> bool:
    """Idempotently patch a file.

    :param path: File to patch.
    :param check: String whose presence means "already patched."
    :param find: Exact string to find.
    :param replace: Replacement string.
    :returns: True if patched, False if already patched or pattern not found.
    """
    content = path.read_text()
    if check in content:
        return False
    if find not in content:
        return False
    content = content.replace(find, replace, 1)
    path.write_text(content)
    return True


def register_with_omnigent() -> dict[str, bool]:
    """Register the Hermes harness in all Omnigent patch points.

    Idempotent — safe to call multiple times.

    :returns: Dict of patch_name → True if changed.
    """
    results = {}
    pkg = _find_omnigent_pkg()
    if pkg is None:
        return {"error": False}

    # 1. _HARNESS_MODULES (runtime/harnesses/__init__.py)
    init_path = pkg / "runtime" / "harnesses" / "__init__.py"
    if init_path.exists():
        entry = '    "hermes": "hermes_omnigent_harness.hermes_harness",'
        content = init_path.read_text()
        if '"hermes"' not in content:
            pattern = r'(_HARNESS_MODULES:\s*dict\[str,\s*str\]\s*=\s*\{.*?)(\n\})'
            match = re.search(pattern, content, re.DOTALL)
            if match:
                new = content[: match.end(1)] + "\n" + entry + content[match.end(1):]
                init_path.write_text(new)
                results["harness_modules"] = True

    # 2. OMNIGENT_HARNESSES (spec/_omnigent_compat.py)
    compat_path = pkg / "spec" / "_omnigent_compat.py"
    if compat_path.exists():
        changed = _patch_file(
            compat_path,
            check='"hermes"',
            find='        "pi",\n    },\n)',
            replace='        "pi",\n        "hermes",\n    },\n)',
        )
        if changed:
            results["omnigent_harnesses"] = True

    # 3. _OS_ENV_HARNESSES (cli.py)
    cli_path = pkg / "cli.py"
    if cli_path.exists():
        changed = _patch_file(
            cli_path,
            check='_OS_ENV_HARNESSES: frozenset[str] = frozenset({"claude-sdk", "codex", "pi", "hermes"})',
            find='_OS_ENV_HARNESSES: frozenset[str] = frozenset({"claude-sdk", "codex", "pi"})',
            replace='_OS_ENV_HARNESSES: frozenset[str] = frozenset({"claude-sdk", "codex", "pi", "hermes"})',
        )
        if changed:
            results["os_env_harnesses"] = True

    # 4. _HARNESS_MODEL_ENV_KEY (runner/app.py)
    app_path = pkg / "runner" / "app.py"
    if app_path.exists():
        changed = _patch_file(
            app_path,
            check='"hermes": "HARNESS_HERMES_MODEL"',
            find='    "openai-agents": "HARNESS_OPENAI_AGENTS_MODEL",\n}',
            replace='    "openai-agents": "HARNESS_OPENAI_AGENTS_MODEL",\n    "hermes": "HARNESS_HERMES_MODEL",\n}',
        )
        if changed:
            results["model_env_key"] = True

    # 5. _build_spawn_env_from_spec dispatch (runner/app.py)
    if app_path.exists():
        content = app_path.read_text()
        if '_build_hermes_spawn_env' not in content:
            # Add dispatch case
            old = '        elif harness == "openai-agents":\n            env = _build_openai_agents_sdk_spawn_env(spec)\n        else:'
            new = '        elif harness == "openai-agents":\n            env = _build_openai_agents_sdk_spawn_env(spec)\n        elif harness == "hermes":\n            env = _build_hermes_spawn_env(spec, workdir=workdir)\n        else:'
            if old in content:
                content = content.replace(old, new, 1)
            # Add import
            old_imp = '            _build_pi_spawn_env,\n        )'
            new_imp = '            _build_pi_spawn_env,\n            _build_hermes_spawn_env,\n        )'
            if old_imp in content:
                content = content.replace(old_imp, new_imp, 1)
            app_path.write_text(content)
            results["spawn_env_dispatch"] = True

    # 6. _build_hermes_spawn_env (runtime/workflow.py)
    wf_path = pkg / "runtime" / "workflow.py"
    if wf_path.exists():
        content = wf_path.read_text()
        if "def _build_hermes_spawn_env" not in content:
            marker = '    os_env_payload = _serialize_os_env(spec.os_env)\n    if os_env_payload is not None:\n        env["HARNESS_PI_OS_ENV"] = os_env_payload\n    return env\n'
            if marker in content:
                hermes_func = _HERMES_SPAWN_ENV_FUNC
                content = content.replace(marker, marker + hermes_func, 1)
                wf_path.write_text(content)
                results["workflow_builder"] = True

    return results


_HERMES_SPAWN_ENV_FUNC = '''


def _build_hermes_spawn_env(
    spec,
    *,
    workdir=None,
):
    """Build env-var dict for the Hermes harness. Hermes manages its own credentials."""
    env = {}
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


def _register_at_startup() -> None:
    """Auto-register on Python startup (called by .pth file).

    Silently does nothing if Omnigent isn't installed.
    """
    try:
        register_with_omnigent()
    except Exception:
        # Never crash Python startup
        pass


def main() -> None:
    """CLI entry point for ``hermes-register``."""
    print("Hermes Omnigent Harness — Registration")
    print()

    pkg = _find_omnigent_pkg()
    if pkg is None:
        print("Error: Omnigent is not installed.", file=sys.stderr)
        print("Install it with: uv tool install omnigent", file=sys.stderr)
        sys.exit(1)

    print(f"Omnigent package: {pkg}")
    print()

    results = register_with_omnigent()

    if not results:
        print("All patches already applied — Hermes harness is registered.")
    else:
        print("Applied patches:")
        for name in sorted(results):
            print(f"  ✓ {name}")
        print()
        print("Registration complete! You can now use:")
        print("  omni run --harness hermes")
        print("  omni run my-agent/ --harness hermes")

    print()
    print("Verify with:")
    print("  python -c 'from omnigent.runtime.harnesses import")
    print("    _HARNESS_MODULES; print(\"hermes\" in _HARNESS_MODULES)'")
