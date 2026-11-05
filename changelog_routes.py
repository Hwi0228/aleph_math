import os
import re
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import render_template

from auth import login_required


GITHUB_REPOSITORY = "Hwi0228/hexora"
CACHE_SECONDS = 60
_CACHE_LOCK = threading.Lock()
_CACHE = {}


def _fetch_remote_changelog(ref):
    encoded_ref = quote(ref, safe="/-._")
    url = (
        f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/"
        f"{encoded_ref}/CHANGELOG.md"
    )
    request = Request(
        url,
        headers={"User-Agent": "HEXORA-changelog/1.0", "Accept": "text/plain"},
    )
    with urlopen(request, timeout=3) as response:
        return response.read().decode("utf-8")


def _read_local_changelog():
    path = Path(__file__).resolve().parent / "CHANGELOG.md"
    return path.read_text(encoding="utf-8")


def _load_changelog():
    ref = os.environ.get("CHANGELOG_GITHUB_REF", "master").strip() or "master"
    version_path = Path(__file__).resolve().parent / "VERSION"
    try:
        current_version = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        current_version = ""
    cache_key = (ref, current_version)
    now = time.monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached["fetched_at"] < CACHE_SECONDS:
            return cached["markdown"], cached["source"]

        try:
            markdown = _fetch_remote_changelog(ref)
            if current_version and f"## [{current_version}]" not in markdown:
                raise ValueError("GitHub changelog does not include the running version.")
            source = "github"
        except (OSError, URLError, TimeoutError, UnicodeError, ValueError):
            markdown = _read_local_changelog()
            source = "local"

        _CACHE[cache_key] = {
            "markdown": markdown,
            "source": source,
            "fetched_at": now,
        }
        return markdown, source


def _parse_changelog(markdown):
    releases = []
    current_release = None
    current_section = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        release_match = re.match(r"^##\s+\[(.+?)\](?:\s+-\s+(.+))?$", line)
        if release_match:
            current_release = {
                "version": release_match.group(1),
                "date": release_match.group(2) or "",
                "sections": [],
            }
            releases.append(current_release)
            current_section = None
            continue

        section_match = re.match(r"^###\s+(.+)$", line)
        if section_match and current_release:
            current_section = {
                "title": section_match.group(1),
                "items": [],
            }
            current_release["sections"].append(current_section)
            continue

        if line.startswith("- ") and current_section:
            current_section["items"].append(line[2:].strip())

    return releases


def changelog():
    markdown, source = _load_changelog()
    ref = os.environ.get("CHANGELOG_GITHUB_REF", "master").strip() or "master"
    github_url = (
        f"https://github.com/{GITHUB_REPOSITORY}/blob/"
        f"{quote(ref, safe='/-._')}/CHANGELOG.md"
    )
    return render_template(
        "changelog.html",
        releases=_parse_changelog(markdown),
        changelog_source=source,
        github_changelog_url=github_url,
        active_page="changelog",
    )


def register_changelog_routes(app):
    app.add_url_rule(
        "/changelog",
        endpoint="changelog",
        view_func=login_required(changelog),
        methods=["GET"],
    )
