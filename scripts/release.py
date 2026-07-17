"""Prepare and publish a cn-scraper-mcp release.

Prerequisites
-------------
1. Install GitHub CLI: https://cli.github.com/
2. Log in with ``gh auth login`` and verify with ``gh auth status``.
3. Work on ``master`` with a clean working tree synchronized with
   ``origin/master``.
4. Record completed changes under the appropriate ``CHANGELOG.md``
   ``[Unreleased]`` headings, such as Added, Changed, Fixed, or Removed.
   ``Planned`` is preserved under ``[Unreleased]`` and is not released.

Recommended two-stage release
-----------------------------
First prepare the version and changelog::

    python scripts/release.py prepare 0.3.0

Review the generated changes before publishing::

    git diff
    python scripts/release.py publish 0.3.0

The publish command runs all local checks and asks for confirmation immediately
before committing, pushing, and creating the GitHub Release.

One-command release
-------------------
When the ``[Unreleased]`` notes are already complete::

    python scripts/release.py release 0.3.0

Failure recovery and automation
-------------------------------
If a local check fails after ``prepare``, fix the problem and rerun::

    python scripts/release.py publish 0.3.0

Use ``--yes`` only in trusted automation to skip the final confirmation.
``--skip-checks`` is an emergency escape hatch; GitHub Actions still runs the
release checks, but skipping the local checks is not recommended.

Publishing model
----------------
The GitHub Release triggers ``.github/workflows/publish.yml``, which publishes
to PyPI through Trusted Publishing. This script never uploads to PyPI directly.
It waits for the workflow and verifies the new version through the PyPI API.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "src" / "cn_scraper_mcp" / "__init__.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"
EXPECTED_RELEASE_FILES = {"CHANGELOG.md", "src/cn_scraper_mcp/__init__.py"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_ASSIGNMENT_RE = re.compile(r'(?m)^__version__ = "(?P<version>[^"]+)"$')


class ReleaseError(RuntimeError):
    """A release precondition or command failed."""


def run(
    *args: str | Path,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    print(f"+ {' '.join(command)}", flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise ReleaseError(
            f"command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    return result


def output(*args: str | Path) -> str:
    return run(*args, capture=True).stdout.strip()


def validate_version(version: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError("version must use stable SemVer form X.Y.Z")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def current_version(text: str | None = None) -> str:
    source = VERSION_FILE.read_text(encoding="utf-8") if text is None else text
    match = VERSION_ASSIGNMENT_RE.search(source)
    if not match:
        raise ReleaseError(f"could not find __version__ in {VERSION_FILE}")
    return match.group("version")


def split_unreleased(changelog: str) -> tuple[str, str, str]:
    marker = "## [Unreleased]"
    start = changelog.find(marker)
    if start < 0:
        raise ReleaseError("CHANGELOG.md has no [Unreleased] section")
    body_start = start + len(marker)
    next_release = re.search(r"(?m)^## \[(?!Unreleased\])", changelog[body_start:])
    if not next_release:
        raise ReleaseError("CHANGELOG.md has no previous release section")
    body_end = body_start + next_release.start()
    return changelog[:start], changelog[body_start:body_end], changelog[body_end:]


def partition_unreleased(body: str) -> tuple[str, str]:
    sections = list(re.finditer(r"(?m)^### (?P<name>[^\r\n]+)\r?$", body))
    if not sections:
        raise ReleaseError("[Unreleased] needs at least one release section such as Added or Changed")

    planned: list[str] = []
    released: list[str] = []
    for index, match in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(body)
        block = body[match.start() : end].strip()
        if match.group("name").strip().casefold() == "planned":
            planned.append(block)
        else:
            released.append(block)

    release_text = "\n\n".join(released).strip()
    if not release_text or "TODO" in release_text.upper():
        raise ReleaseError("[Unreleased] has no complete release notes (Planned is not released)")
    return "\n\n".join(planned).strip(), release_text


def update_changelog(changelog: str, version: str, today: date) -> str:
    if re.search(rf"(?m)^## \[{re.escape(version)}\]", changelog):
        raise ReleaseError(f"CHANGELOG.md already contains {version}")

    prefix, unreleased_body, rest = split_unreleased(changelog)
    planned, release_notes = partition_unreleased(unreleased_body)
    new_unreleased = "## [Unreleased]\n"
    if planned:
        new_unreleased += f"\n{planned}\n"
    release_section = f"\n## [{version}] - {today.isoformat()}\n\n{release_notes}\n\n"
    updated = prefix + new_unreleased + release_section + rest.lstrip()

    link_match = re.search(
        r"(?m)^\[Unreleased\]: https://github\.com/(?P<repo>[^/]+/[^/]+)/compare/"
        r"v(?P<previous>\d+\.\d+\.\d+)\.\.\.HEAD$",
        updated,
    )
    if not link_match:
        raise ReleaseError("CHANGELOG.md has no recognized [Unreleased] comparison link")
    repo = link_match.group("repo")
    previous = link_match.group("previous")
    replacement = (
        f"[Unreleased]: https://github.com/{repo}/compare/v{version}...HEAD\n"
        f"[{version}]: https://github.com/{repo}/compare/v{previous}...v{version}"
    )
    return updated[: link_match.start()] + replacement + updated[link_match.end() :]


def release_notes(changelog: str, version: str) -> str:
    match = re.search(
        rf"(?ms)^## \[{re.escape(version)}\](?: - [^\r\n]+)?\r?\n\s*(.*?)"
        r"(?=^## \[|\Z)",
        changelog,
    )
    if not match:
        raise ReleaseError(f"CHANGELOG.md has no {version} release section")
    notes = match.group(1).strip()
    if not notes or "TODO" in notes.upper():
        raise ReleaseError(f"release notes for {version} are empty or contain TODO")
    return notes


def prepare(version: str) -> None:
    target = validate_version(version)
    ensure_clean_worktree()
    old_version_text = VERSION_FILE.read_text(encoding="utf-8")
    old_version = current_version(old_version_text)
    if target <= validate_version(old_version):
        raise ReleaseError(f"new version {version} must be greater than {old_version}")

    old_changelog = CHANGELOG_FILE.read_text(encoding="utf-8")
    new_version_text = VERSION_ASSIGNMENT_RE.sub(
        f'__version__ = "{version}"', old_version_text, count=1
    )
    new_changelog = update_changelog(old_changelog, version, date.today())
    VERSION_FILE.write_text(new_version_text, encoding="utf-8")
    CHANGELOG_FILE.write_text(new_changelog, encoding="utf-8")
    print(f"Prepared v{version}. Review CHANGELOG.md, then run:")
    print(f"  {sys.executable} scripts/release.py publish {version}")


def porcelain_files() -> set[str]:
    # Do not use output(), whose strip() would remove the first status
    # column's leading space and turn " M CHANGELOG.md" into "HANGELOG.md".
    lines = run("git", "status", "--porcelain=v1", capture=True).stdout.splitlines()
    files: set[str] = set()
    for line in lines:
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.add(path.replace("\\", "/"))
    return files


def ensure_clean_worktree() -> None:
    dirty = porcelain_files()
    if dirty:
        raise ReleaseError("working tree must be clean: " + ", ".join(sorted(dirty)))


def ensure_publish_worktree() -> bool:
    dirty = porcelain_files()
    unexpected = dirty - EXPECTED_RELEASE_FILES
    if unexpected:
        raise ReleaseError("unrelated working-tree changes found: " + ", ".join(sorted(unexpected)))
    if dirty and dirty != EXPECTED_RELEASE_FILES:
        missing = EXPECTED_RELEASE_FILES - dirty
        raise ReleaseError("prepared release is incomplete; missing changes: " + ", ".join(sorted(missing)))
    return bool(dirty)


def verify_git(version: str, branch: str) -> str:
    if output("git", "branch", "--show-current") != branch:
        raise ReleaseError(f"release must run from branch {branch!r}")
    run("git", "fetch", "origin", branch, "--tags")
    head = output("git", "rev-parse", "HEAD")
    remote = output("git", "rev-parse", f"origin/{branch}")
    if head != remote:
        raise ReleaseError(f"local {branch} must match origin/{branch} before release")
    tag = f"v{version}"
    if run("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode == 0:
        raise ReleaseError(f"tag {tag} already exists")
    return head


def require_gh() -> None:
    if not shutil.which("gh"):
        raise ReleaseError("GitHub CLI is required: https://cli.github.com/")
    run("gh", "auth", "status")


def run_release_checks(version: str) -> None:
    python = sys.executable
    run(python, "-m", "ruff", "check", "src", "tests", "scripts")
    run(python, "-W", "error::RuntimeWarning", "-m", "pytest", "tests/", "-q")
    run(python, "scripts/mcp_smoke_test.py")
    run(python, "scripts/platform_health.py", "--mock", "--json")
    run("git", "diff", "--check")

    with tempfile.TemporaryDirectory(prefix="cn-scraper-release-") as directory:
        dist = Path(directory) / "dist"
        run(python, "-m", "build", "--outdir", dist)
        artifacts = sorted(dist.iterdir())
        if not artifacts:
            raise ReleaseError("build produced no distributions")
        run(python, "-m", "twine", "check", *artifacts)
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise ReleaseError(f"expected one wheel, found {len(wheels)}")

        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        venv_python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run(venv_python, "-m", "pip", "install", "--no-deps", wheels[0])
        code = (
            "import cn_scraper_mcp; "
            f"assert cn_scraper_mcp.__version__ == {version!r}, cn_scraper_mcp.__version__"
        )
        run(venv_python, "-c", code)


def confirm(version: str, branch: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise ReleaseError("interactive confirmation unavailable; pass --yes explicitly")
    answer = input(f"Publish v{version} from {branch} to GitHub and PyPI? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise ReleaseError("release cancelled")


def find_workflow_run(version: str, head_sha: str, timeout: int = 90) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = output(
            "gh",
            "run",
            "list",
            "--workflow",
            "publish.yml",
            "--event",
            "release",
            "--limit",
            "20",
            "--json",
            "databaseId,displayTitle,headSha",
        )
        for item in json.loads(raw):
            if item.get("displayTitle") == f"v{version}" and item.get("headSha") == head_sha:
                return int(item["databaseId"])
        time.sleep(3)
    raise ReleaseError("GitHub publish workflow did not appear within 90 seconds")


def verify_pypi(version: str, timeout: int = 120) -> None:
    url = f"https://pypi.org/pypi/cn-scraper-mcp/{version}/json"
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": "cn-scraper-mcp-release"})
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
            if payload.get("info", {}).get("version") == version:
                print(f"PyPI verified: https://pypi.org/project/cn-scraper-mcp/{version}/")
                return
        except (urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(5)
    raise ReleaseError(f"PyPI did not expose {version} within {timeout} seconds")


def publish(version: str, branch: str, assume_yes: bool, skip_checks: bool) -> None:
    validate_version(version)
    if current_version() != version:
        raise ReleaseError(f"package version is {current_version()}, expected {version}")
    notes = release_notes(CHANGELOG_FILE.read_text(encoding="utf-8"), version)
    needs_commit = ensure_publish_worktree()
    head_before_commit = verify_git(version, branch)
    require_gh()
    if not skip_checks:
        run_release_checks(version)
    confirm(version, branch, assume_yes)

    if needs_commit:
        run("git", "add", "--", *sorted(EXPECTED_RELEASE_FILES))
        run("git", "commit", "-m", f"chore: prepare v{version}")
        head_sha = output("git", "rev-parse", "HEAD")
        run("git", "push", "origin", branch)
    else:
        head_sha = head_before_commit

    tag = f"v{version}"
    run(
        "gh",
        "release",
        "create",
        tag,
        "--target",
        branch,
        "--title",
        tag,
        "--notes",
        notes,
        "--latest",
    )
    run_id = find_workflow_run(version, head_sha)
    run("gh", "run", "watch", str(run_id), "--exit-status")
    verify_pypi(version)
    print(f"Release v{version} completed successfully.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = result.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="update version and CHANGELOG.md")
    prepare_parser.add_argument("version")

    for name, help_text in (
        ("publish", "test and publish an already prepared release"),
        ("release", "prepare, test, and publish in one command"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("version")
        command.add_argument("--branch", default="master")
        command.add_argument("--yes", action="store_true", help="skip the final confirmation")
        command.add_argument(
            "--skip-checks",
            action="store_true",
            help="skip local checks (GitHub Actions still runs them)",
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.version)
        elif args.command == "publish":
            publish(args.version, args.branch, args.yes, args.skip_checks)
        else:
            prepare(args.version)
            publish(args.version, args.branch, args.yes, args.skip_checks)
    except (ReleaseError, OSError, json.JSONDecodeError) as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
