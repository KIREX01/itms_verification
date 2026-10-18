#!/usr/bin/env python3
"""
ITMS Verification Copilot - Dual-Platform Release Packager.

Automates building distribution packages for:
1. Windows Desktop Client: dist/itms-v<version>-windows.zip
2. Linux Server / VPS:    dist/itms-v<version>-linux.tar.gz

Usage:
    python scripts/package_release.py               # Builds both Windows and Linux packages
    python scripts/package_release.py --target win  # Builds only Windows ZIP
    python scripts/package_release.py --target lin  # Builds only Linux tarball
    python scripts/package_release.py --version 1.0.8
"""
import argparse
import os
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.version import __version__

# Safe UTF-8 console output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Common exclusions across all distributions
GLOBAL_EXCLUDES = {
    ".git",
    ".github",
    ".venv",
    "venv",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "secure",
    ".env",
    "db.sqlite3",
    "media/vault",
    "media/crops",
    "media/itms_web.log",
    ".itms_web.pid",
}

# Windows-only assets (omitted from Linux tarball)
WINDOWS_ONLY_FILES = {
    "itms.cmd",
    "itms.ps1",
    "run.bat",
    "run_https_local.bat",
    "install.ps1",
    "uninstall.ps1",
    "install_service.bat",
    "uninstall_service.bat",
    "windows_service.py",
}

# Linux-only assets (omitted from Windows ZIP)
LINUX_ONLY_FILES = {
    "itms",
    "install.sh",
    "uninstall.sh",
    "deploy_user_data.sh",
    "scripts/setup_vps.sh",
}

# Executable scripts given 0o755 permissions in Linux tarball
EXECUTABLE_SCRIPTS = {
    "itms",
    "install.sh",
    "uninstall.sh",
    "deploy_user_data.sh",
    "setup_vps.sh",
    "bootstrap.py",
    "manage.py",
}


def should_exclude(rel_path_str: str, target: str) -> bool:
    """Returns True if the given relative path should be excluded from the archive."""
    normalized = rel_path_str.replace("\\", "/").strip("/")

    # Check global exclusions
    for exc in GLOBAL_EXCLUDES:
        if normalized == exc or normalized.startswith(f"{exc}/"):
            return True
        if "/__pycache__" in normalized or normalized.endswith((".pyc", ".pyo")):
            return True

    # Target-specific exclusions
    if target == "windows" and normalized in LINUX_ONLY_FILES:
        return True
    if target == "linux" and normalized in WINDOWS_ONLY_FILES:
        return True

    return False


SAFE_NAME_REGEX = r"[^0-9a-zA-Z\.\-_]"


def build_windows_package(version: str, output_dir: Path) -> Path:
    """Packages the Windows desktop distribution into a ZIP archive."""
    safe_version = re.sub(SAFE_NAME_REGEX, "", version)
    resolved_out = output_dir.resolve()
    archive_name = f"itms-v{safe_version}-windows.zip"
    archive_path = (resolved_out / archive_name).resolve()
    if not str(archive_path).startswith(str(resolved_out)):
        raise ValueError(f"Invalid output path: {archive_path}")

    print(f"[*] Packaging Windows release: {archive_path.name}...")

    file_count = 0
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            # Prune directories in-place
            dirs[:] = [
                d for d in dirs
                if not should_exclude(str(Path(root, d).relative_to(PROJECT_ROOT)), "windows")
            ]
            for f in files:
                full_path = Path(root, f).resolve()
                rel_path = full_path.relative_to(PROJECT_ROOT)
                if should_exclude(str(rel_path), "windows"):
                    continue
                # Place inside top-level folder 'itms_verification/' inside archive
                arc_entry = f"itms_verification/{rel_path.as_posix()}"
                norm_entry = os.path.normpath(arc_entry).replace("\\", "/")
                if norm_entry.startswith("/") or ".." in norm_entry.split("/"):
                    continue
                zf.write(full_path, arcname=norm_entry)
                file_count += 1

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"[OK] Windows package ready: {archive_name} ({size_mb:.2f} MB, {file_count} files)")
    return archive_path


def build_linux_package(version: str, output_dir: Path) -> Path:
    """Packages the Linux server distribution into a compressed tarball."""
    safe_version = re.sub(SAFE_NAME_REGEX, "", version)
    resolved_out = output_dir.resolve()
    archive_name = f"itms-v{safe_version}-linux.tar.gz"
    archive_path = (resolved_out / archive_name).resolve()
    if not str(archive_path).startswith(str(resolved_out)):
        raise ValueError(f"Invalid output path: {archive_path}")

    print(f"[*] Packaging Linux release: {archive_path.name}...")

    file_count = 0
    with tarfile.open(archive_path, "w:gz") as tf:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [
                d for d in dirs
                if not should_exclude(str(Path(root, d).relative_to(PROJECT_ROOT)), "linux")
            ]
            for f in files:
                full_path = Path(root, f).resolve()
                rel_path = full_path.relative_to(PROJECT_ROOT)
                if should_exclude(str(rel_path), "linux"):
                    continue

                arc_entry = f"itms_verification/{rel_path.as_posix()}"
                norm_entry = os.path.normpath(arc_entry).replace("\\", "/")
                if norm_entry.startswith("/") or ".." in norm_entry.split("/"):
                    continue

                def _linux_mode_filter(tar_info: tarfile.TarInfo, file_name: str = rel_path.name) -> tarfile.TarInfo:
                    if file_name in EXECUTABLE_SCRIPTS:
                        tar_info.mode = 0o755
                    return tar_info

                tf.add(str(full_path), arcname=norm_entry, recursive=False, filter=_linux_mode_filter)
                file_count += 1

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print(f"[OK] Linux package ready: {archive_name} ({size_mb:.2f} MB, {file_count} files)")
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="ITMS Verification Copilot Dual-Platform Release Packager")
    parser.add_argument("--version", default=__version__, help=f"Release version tag (default: {__version__})")
    parser.add_argument("--target", choices=["all", "windows", "win", "linux", "lin"], default="all", help="Target platform to package")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "dist"), help="Output directory for build archives")
    args = parser.parse_args()

    version = re.sub(r"[^0-9a-zA-Z\.\-_]", "", args.version.lstrip("vV"))
    output_dir = Path(args.out).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    target = args.target.lower()
    print("=" * 65)
    print(f"  ITMS VERIFICATION COPILOT - RELEASE PACKAGER v{version}")
    print("=" * 65)

    if target in ("all", "windows", "win"):
        build_windows_package(version, output_dir)
    if target in ("all", "linux", "lin"):
        build_linux_package(version, output_dir)

    print("\n[+] Distribution packages generated in:")
    print(f"    {output_dir.resolve()}")


if __name__ == "__main__":
    main()
