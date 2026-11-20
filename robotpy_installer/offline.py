"""Passive inspection of offline dependency bundles."""

from pathlib import Path

from ._bundle_format import artifact_role, validate_bundle


def inspect_bundle(path: Path) -> str:
    """Return a validated inventory without importing any bundled code."""
    manifest = validate_bundle(path)
    local = manifest["local_python"]
    robot = manifest["robot_python"]
    platform = manifest["platform"]
    lines = [
        f"Offline bundle format {manifest['format_version']} (creator {manifest['creator_version']})",
        f"Local Python: {'.'.join(map(str, local['version']))} ({local['implementation']}, ABI flags: {local['abi_flags']!r})",
        f"Local platform: {platform['sys_platform']} / {platform['machine']} / {platform['sysconfig_platform']}",
        f"Robot Python: {'.'.join(map(str, robot['version']))}",
        f"Robot target: {robot['platform']}",
        f"Robot runtime: {robot['runtime_path']}",
        f"Cache year: {manifest['cache_year']}",
    ]
    for role, heading in (
        ("local-wheel", "Local wheels"),
        ("robot-wheel", "Robot wheels"),
        ("robot-cache", "Robot cache (non-wheel payloads)"),
        ("pip-tool", "Pip tooling"),
    ):
        lines.extend(("", heading + ":"))
        entries = sorted(
            (
                entry
                for entry in manifest["artifacts"]
                if artifact_role(entry["path"]) == role
            ),
            key=lambda entry: entry["path"],
        )
        for entry in entries:
            if role == "robot-cache":
                lines.append(f"  {entry['path']} ({entry['size']} bytes)")
            else:
                lines.append(f"  {entry['name']} {entry['version']} ({entry['path']})")
        if not entries:
            lines.append("  (none)")
    return "\n".join(lines)
