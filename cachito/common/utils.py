# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import urllib


def b64encode(s: bytes) -> str:
    """Encode a bytes string in base64."""
    return base64.b64encode(s).decode("utf-8")


def get_repo_name(url):
    """Get the repo name from the URL."""
    parsed_url = urllib.parse.urlparse(url)
    repo = parsed_url.path.strip("/")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return repo
