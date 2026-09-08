"""User config for cloud-archive Wiki targets.

Stored at ``%USERPROFILE%/.copilot/notepad-copilot/wiki.json``::

    {
      "profiles": [
        {
          "name": "Mooncake Pod Wiki",
          "organization": "https://dev.azure.com/myorg",
          "project": "MyProject",
          "wiki_identifier": "MyProject.wiki",
          "parent_path": "/Cases",
          "api_version": "7.0"
        }
      ],
      "default": "Mooncake Pod Wiki"
    }
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~")) / ".copilot" / "notepad-copilot"
CONFIG_PATH = CONFIG_DIR / "wiki.json"


def parse_wiki_url(url: str) -> dict:
    """Parse an ADO Wiki URL into its component fields.

    Recognises forms like:
      https://dev.azure.com/{org}/{project}/_wiki/wikis/{wiki}[/...]
      https://dev.azure.com/{org}/{project}/_wiki/wikis/{wiki}/{pageId}/{Slug}
      https://dev.azure.com/{org}/{project}/_wiki/wikis/{wiki}?pagePath=%2F...
      https://{org}.visualstudio.com/{project}/_wiki/wikis/{wiki}/...

    Returns a dict possibly containing:
      organization, project, wiki_identifier, parent_path
    Unknown fields are omitted; safe to call on non-URL text (returns {}).
    """
    from urllib.parse import urlparse, parse_qs, unquote

    s = (url or "").strip()
    if not s.lower().startswith(("http://", "https://")):
        return {}
    try:
        u = urlparse(s)
    except Exception:
        return {}
    host = (u.netloc or "").lower()
    parts = [p for p in (u.path or "").split("/") if p]

    out: dict = {}
    project_idx = -1
    wiki_idx = -1
    if "dev.azure.com" in host and len(parts) >= 1:
        out["organization"] = f"https://dev.azure.com/{parts[0]}"
        if len(parts) >= 2 and parts[1] != "_wiki":
            out["project"] = parts[1]
            project_idx = 1
        if "_wiki" in parts:
            i = parts.index("_wiki")
            if i + 2 < len(parts) and parts[i + 1] == "wikis":
                out["wiki_identifier"] = parts[i + 2]
                wiki_idx = i + 2
    elif host.endswith("visualstudio.com") and len(parts) >= 1:
        org = host.split(".visualstudio.com")[0]
        out["organization"] = f"https://{org}.visualstudio.com"
        if parts[0] != "_wiki":
            out["project"] = parts[0]
            project_idx = 0
        if "_wiki" in parts:
            i = parts.index("_wiki")
            if i + 2 < len(parts) and parts[i + 1] == "wikis":
                out["wiki_identifier"] = parts[i + 2]
                wiki_idx = i + 2

    # ----- parent_path extraction -----
    # 1) query string pagePath=/...
    qs = parse_qs(u.query or "")
    if "pagePath" in qs and qs["pagePath"]:
        out["parent_path"] = unquote(qs["pagePath"][0])
        return out
    # 2) /{pageId}/{slug}/{slug}/...  after wiki name
    if wiki_idx >= 0 and wiki_idx + 1 < len(parts):
        tail = parts[wiki_idx + 1:]
        # First segment is the page id (numeric). It is the ONLY reliable
        # identifier of the page in a friendly ADO Wiki URL — the slug that
        # follows is just the leaf page's title with special chars encoded
        # and ancestors omitted, so it cannot be trusted as a full path.
        # Callers should resolve the real path via the API using page_id.
        if tail and tail[0].isdigit():
            out["page_id"] = tail[0]
            tail = tail[1:]
        if tail:
            # Slugs: dashes commonly replace spaces; URL-decode each
            segs = [unquote(s).replace("-", " ") for s in tail]
            out["parent_path"] = "/" + "/".join(segs)
    return out


@dataclass
class WikiProfile:
    name: str = ""
    organization: str = ""        # e.g. https://dev.azure.com/myorg
    project: str = ""
    wiki_identifier: str = ""     # wiki name or id
    parent_path: str = "/Cases"   # page parent
    api_version: str = "7.0"

    def is_complete(self) -> bool:
        return not self.missing_fields()

    def missing_fields(self) -> list[str]:
        miss = []
        if not self.name:
            miss.append("Name")
        if not self.organization:
            miss.append("Organization URL")
        if not self.project:
            miss.append("Project")
        if not self.wiki_identifier:
            miss.append("Wiki Identifier")
        return miss


@dataclass
class WikiConfig:
    profiles: list[WikiProfile] = field(default_factory=list)
    default: str = ""
    last_url: str = ""           # last URL used in cloud-archive dialog
    last_parent_path: str = ""   # last parent path picked


def load_config() -> WikiConfig:
    if not CONFIG_PATH.is_file():
        return WikiConfig()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return WikiConfig()
    profiles = []
    for p in raw.get("profiles", []) or []:
        profiles.append(WikiProfile(
            name=p.get("name", ""),
            organization=p.get("organization", ""),
            project=p.get("project", ""),
            wiki_identifier=p.get("wiki_identifier", ""),
            parent_path=p.get("parent_path", "/Cases") or "/",
            api_version=p.get("api_version", "7.0") or "7.0",
        ))
    return WikiConfig(
        profiles=profiles,
        default=raw.get("default", ""),
        last_url=raw.get("last_url", ""),
        last_parent_path=raw.get("last_parent_path", ""),
    )


def save_config(cfg: WikiConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "profiles": [asdict(p) for p in cfg.profiles],
        "default": cfg.default,
        "last_url": cfg.last_url,
        "last_parent_path": cfg.last_parent_path,
    }
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_default_profile(cfg: WikiConfig) -> WikiProfile | None:
    if not cfg.profiles:
        return None
    if cfg.default:
        for p in cfg.profiles:
            if p.name == cfg.default:
                return p
    return cfg.profiles[0]
