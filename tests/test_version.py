"""The version a user sees, the changelog and the tags agree: the version may lead the newest tag (unreleased work on
main), never trail it, and the changelog's top section is always the version."""
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _v(s):
    return tuple(int(x) for x in s.split("."))


def test_version_changelog_and_tags_agree():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    top = re.search(r"^## (\d+\.\d+(?:\.\d+)?)$", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert top and top.group(1) == version
    tags = subprocess.run(["git", "tag", "--merged", "HEAD", "--list", "v*"], cwd=ROOT, capture_output=True,
                          text=True).stdout.split()
    versions = [_v(t[1:]) if t.count(".") == 2 else _v(t[1:] + ".0") for t in tags if re.fullmatch(r"v\d+(\.\d+){1,2}", t)]
    if versions:
        assert _v(version) >= max(versions)
