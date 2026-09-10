#!/usr/bin/env python3
"""
Archivist - Python Dependencies Installation Script

This script installs Python dependencies for the monorepo using the new
hierarchical dependency structure:
1. Root shared dependencies
2. Shared utilities package dependencies
3. Individual app-specific dependencies

Usage:
    python install-python-deps.py [--app APP_NAME] [--dev] [--upgrade]

Examples:
    python install-python-deps.py                    # Install all dependencies
    python install-python-deps.py --app archivist-api  # Install for specific app
    python install-python-deps.py --dev             # Include dev dependencies
    python install-python-deps.py --upgrade         # Upgrade existing packages
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Define the monorepo structure
PYTHON_APPS = {
    "archivist-api": "apps/archivist-api",
    "functions": "apps/functions",
}

SHARED_PACKAGES = {"shared-utils": "packages/shared-utils"}


# Status returned by run_pip_install so callers can distinguish a genuine
# failure from an optional layer that simply does not exist in this repo.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def run_pip_install(requirements_file, upgrade=False, description="", optional=False):
    """Run pip install for a requirements file.

    Returns one of STATUS_OK / STATUS_FAILED / STATUS_SKIPPED. When ``optional``
    is True a missing requirements file is treated as a skip (not a failure) so
    that absent root/shared layers do not break the deployment hook.
    """
    if not os.path.exists(requirements_file):
        if optional:
            print(f"⏭️  Skipping (no requirements file): {requirements_file}")
            return STATUS_SKIPPED
        print(f"⚠️  Requirements file not found: {requirements_file}")
        return STATUS_FAILED

    print(f"📦 Installing {description}...")
    print(f"   File: {requirements_file}")

    cmd = [sys.executable, "-m", "pip", "install", "-r", requirements_file]
    if upgrade:
        cmd.append("--upgrade")

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"✅ Successfully installed {description}")
        return STATUS_OK
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install {description}")
        print(f"   Error: {e.stderr}")
        return STATUS_FAILED


def install_root_dependencies(upgrade=False):
    """Install root shared dependencies (optional — absent in this layout)."""
    return run_pip_install(
        "requirements.txt", upgrade, "root shared dependencies", optional=True
    )


def install_shared_package_dependencies(package_name, upgrade=False):
    """Install dependencies for a shared package (optional layer)."""
    package_path = SHARED_PACKAGES.get(package_name)
    if not package_path:
        print(f"❌ Unknown shared package: {package_name}")
        return STATUS_FAILED

    requirements_file = os.path.join(package_path, "requirements.txt")
    return run_pip_install(
        requirements_file, upgrade, f"{package_name} dependencies", optional=True
    )


def install_app_dependencies(app_name, upgrade=False):
    """Install dependencies for a specific app (required)."""
    app_path = PYTHON_APPS.get(app_name)
    if not app_path:
        print(f"❌ Unknown app: {app_name}")
        return STATUS_FAILED

    requirements_file = os.path.join(app_path, "requirements.txt")
    return run_pip_install(requirements_file, upgrade, f"{app_name} dependencies")


def main():
    parser = argparse.ArgumentParser(
        description="Install Python dependencies for Archivist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--app",
        choices=list(PYTHON_APPS.keys()),
        help="Install dependencies for a specific app only",
    )

    parser.add_argument(
        "--dev",
        action="store_true",
        help="Include development dependencies (already included in root)",
    )

    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade existing packages to latest versions",
    )

    args = parser.parse_args()

    print("🚀 Archivist App - Python Dependencies Installation")
    print("=" * 60)

    results = []

    if args.app:
        # Install for specific app only
        print(f"Installing dependencies for {args.app} only...")

        # 1. Root dependencies (optional layer)
        results.append(install_root_dependencies(args.upgrade))

        # 2. Shared utilities (optional layer)
        results.append(install_shared_package_dependencies("shared-utils", args.upgrade))

        # 3. App-specific dependencies (required)
        results.append(install_app_dependencies(args.app, args.upgrade))
    else:
        # Install all dependencies
        print("Installing all dependencies...")

        # 1. Root dependencies (optional layer)
        results.append(install_root_dependencies(args.upgrade))

        # 2. Shared packages (optional layer)
        for package_name in SHARED_PACKAGES:
            results.append(install_shared_package_dependencies(package_name, args.upgrade))

        # 3. All apps (required)
        for app_name in PYTHON_APPS:
            results.append(install_app_dependencies(app_name, args.upgrade))

    installed = results.count(STATUS_OK)
    skipped = results.count(STATUS_SKIPPED)
    failed = results.count(STATUS_FAILED)

    print("=" * 60)
    print(
        f"📊 Installation Summary: {installed} installed, "
        f"{skipped} skipped, {failed} failed"
    )

    if failed == 0:
        print("🎉 Python dependencies installed successfully!")
        return 0

    print("⚠️  Some required dependencies failed to install. Check the output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())