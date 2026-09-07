"""Upload a redacted archive to an Azure DevOps Wiki.

Strategy:
  1. Take the directory produced by ``core.archive.archive_session``
     (which already redacted PII and rewrote image refs to ``assets/...``).
  2. Upload every file under ``assets/`` to the wiki ``attachments``
     endpoint and remember the returned URL for each one.
  3. Rewrite ``assets/<name>`` references in the markdown to those URLs.
  4. PUT the resulting markdown to the configured wiki page path
     (creating or updating).

Auth: uses ``az account get-access-token --resource <ADO scope>`` so the
user only needs to be logged in via az; no PAT management.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.wiki_config import WikiProfile

# Azure DevOps OAuth resource id (same in global + national clouds)
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


# --------------------------------------------------------------------------
# Token
# --------------------------------------------------------------------------

def get_access_token() -> str:
    """Use `az` to fetch an Azure DevOps access token for the active acct."""
    try:
        cp = subprocess.run(
            ["az", "account", "get-access-token",
             "--resource", ADO_RESOURCE, "-o", "json"],
            capture_output=True, text=True, shell=True, timeout=30,
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "未找到 az CLI，无法获取 ADO token；请先安装 Azure CLI 并 az login。"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("az get-access-token 超时") from e
    if cp.returncode != 0:
        raise RuntimeError(
            f"az get-access-token 失败 (rc={cp.returncode}):\n"
            f"{cp.stderr.strip() or cp.stdout.strip()}"
        )
    try:
        data = json.loads(cp.stdout)
        return data["accessToken"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法解析 az 输出: {e}\n{cp.stdout[:500]}") from e


# --------------------------------------------------------------------------
# Low-level HTTP
# --------------------------------------------------------------------------

@dataclass
class WikiResult:
    page_url: str
    page_path: str
    attachments_uploaded: int
    page_updated: bool   # True = updated existing, False = created new


def _request(method: str, url: str, *, headers: dict, body: bytes | None,
             timeout: int = 60) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method=method, data=body)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        return e.code, dict(e.headers or {}), body_bytes


def _api_base(profile: WikiProfile) -> str:
    org = profile.organization.rstrip("/")
    project = urllib.parse.quote(profile.project, safe="")
    wiki = urllib.parse.quote(profile.wiki_identifier, safe="")
    return f"{org}/{project}/_apis/wiki/wikis/{wiki}"


def _ui_base(profile: WikiProfile) -> str:
    org = profile.organization.rstrip("/")
    project = urllib.parse.quote(profile.project, safe="")
    wiki = urllib.parse.quote(profile.wiki_identifier, safe="")
    return f"{org}/{project}/_wiki/wikis/{wiki}"


# --------------------------------------------------------------------------
# Wiki ops
# --------------------------------------------------------------------------

def upload_attachment(profile: WikiProfile, token: str,
                      file_path: Path, name: str) -> str:
    """Upload one file as a wiki attachment, return relative path
    like ``/.attachments/img-001.png`` that can be used in markdown.
    """
    url = (
        f"{_api_base(profile)}/attachments"
        f"?name={urllib.parse.quote(name, safe='')}"
        f"&api-version={profile.api_version}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Accept": "application/json",
    }
    code, _hdrs, body = _request(
        "PUT", url, headers=headers, body=file_path.read_bytes(),
        timeout=120,
    )
    if code in (200, 201):
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            # API returns: {"name": "...", "path": "/.attachments/..."}
            return data.get("path") or f"/.attachments/{name}"
        except Exception:
            return f"/.attachments/{name}"
    if code == 409:
        # Already exists — just point to the canonical URL
        return f"/.attachments/{name}"
    raise RuntimeError(
        f"上传附件 {name} 失败 (HTTP {code}): "
        f"{body.decode('utf-8', errors='replace')[:400]}"
    )


def get_page_etag(profile: WikiProfile, token: str,
                  page_path: str) -> str | None:
    """Return ETag if page already exists, else None."""
    url = (
        f"{_api_base(profile)}/pages"
        f"?path={urllib.parse.quote(page_path, safe='/')}"
        f"&api-version={profile.api_version}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    code, hdrs, _body = _request(
        "GET", url, headers=headers, body=None, timeout=30,
    )
    if code == 200:
        # ETag is in headers; some servers use 'ETag', some 'eTag'
        for k in ("ETag", "etag", "eTag"):
            if k in hdrs:
                return hdrs[k].strip().strip('"')
        return None
    return None


def get_page_path_by_id(profile: WikiProfile, token: str,
                        page_id: str) -> str | None:
    """Return the real full path of a wiki page given its numeric id.

    Friendly ADO Wiki URLs (``/_wiki/wikis/<wiki>/<id>/<slug>``) only carry
    the page id plus the leaf slug; the slug loses ancestors and encodes
    special characters, so it cannot be turned back into the page's path by
    string munging. The page id, however, uniquely identifies the page, and
    the REST API returns its authoritative ``path``.
    """
    if not page_id:
        return None
    url = (
        f"{_api_base(profile)}/pages/{urllib.parse.quote(str(page_id), safe='')}"
        f"?api-version={profile.api_version}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    code, _hdrs, body = _request(
        "GET", url, headers=headers, body=None, timeout=30,
    )
    if code == 200:
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            path = data.get("path")
            return path or None
        except Exception:
            return None
    return None


def put_page(profile: WikiProfile, token: str,
             page_path: str, content: str) -> tuple[int, str]:
    """Create or update a wiki page. Returns (status_code, body_str)."""
    url = (
        f"{_api_base(profile)}/pages"
        f"?path={urllib.parse.quote(page_path, safe='/')}"
        f"&api-version={profile.api_version}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    etag = get_page_etag(profile, token, page_path)
    if etag:
        headers["If-Match"] = etag
    body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    code, _hdrs, resp = _request(
        "PUT", url, headers=headers, body=body, timeout=60,
    )
    return code, resp.decode("utf-8", errors="replace")


def ensure_ancestor_pages(
    profile: WikiProfile,
    token: str,
    page_path: str,
    *,
    placeholder: str = "_(自动生成的占位页面)_\n",
) -> list[str]:
    """Ensure every ancestor page of ``page_path`` exists.

    ADO Wiki's PUT pages API rejects the request when any ancestor of the
    target page is missing (``WikiAncestorPageNotFoundException``).

    For each ancestor whose GET returns no ETag, we PUT an empty page so
    that subsequent levels can be created. The leaf itself is *not*
    created here — the caller still PUTs the real content afterward.

    Returns the list of ancestor paths that were created (for logging).
    """
    if not page_path or not page_path.startswith("/"):
        return []
    parts = [p for p in page_path.split("/") if p]
    if len(parts) <= 1:
        return []  # top-level page; no ancestor to create
    created: list[str] = []
    # Walk all but the last segment
    for i in range(1, len(parts)):
        ancestor = "/" + "/".join(parts[:i])
        if get_page_etag(profile, token, ancestor) is not None:
            continue  # already exists
        # PUT an empty placeholder page (no If-Match header for create)
        url = (
            f"{_api_base(profile)}/pages"
            f"?path={urllib.parse.quote(ancestor, safe='/')}"
            f"&api-version={profile.api_version}"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        }
        body = json.dumps(
            {"content": placeholder}, ensure_ascii=False
        ).encode("utf-8")
        code, _hdrs, resp = _request(
            "PUT", url, headers=headers, body=body, timeout=60,
        )
        if code in (200, 201):
            created.append(ancestor)
        else:
            raise RuntimeError(
                f"创建父页面 '{ancestor}' 失败 (HTTP {code}):\n"
                f"{resp.decode('utf-8', errors='replace')[:400]}"
            )
    return created


# --------------------------------------------------------------------------
# Top-level: upload a whole archive directory
# --------------------------------------------------------------------------

def upload_archive(
    profile: WikiProfile,
    archive_md_path: Path,
    page_path: str,
    *,
    attachment_prefix: str = "",
) -> WikiResult:
    """Upload archive.md (+ assets/) to ``page_path`` on the wiki.

    ``attachment_prefix`` is prepended to every uploaded attachment's
    filename so multiple cases don't collide (e.g. case_id + timestamp).
    """
    if not profile.is_complete():
        raise ValueError("Wiki 配置不完整：name/org/project/wiki 必填")

    archive_md_path = Path(archive_md_path)
    if not archive_md_path.is_file():
        raise FileNotFoundError(archive_md_path)

    archive_dir = archive_md_path.parent
    assets_dir = archive_dir / "assets"

    token = get_access_token()

    # 1) Upload attachments and build assets/<name> -> wiki path map
    asset_map: dict[str, str] = {}
    n_attached = 0
    if assets_dir.is_dir():
        for f in sorted(assets_dir.iterdir()):
            if not f.is_file():
                continue
            up_name = (
                f"{attachment_prefix}-{f.name}" if attachment_prefix
                else f.name
            )
            wiki_path = upload_attachment(profile, token, f, up_name)
            asset_map[f"assets/{f.name}"] = wiki_path
            asset_map[f"./assets/{f.name}"] = wiki_path
            n_attached += 1

    # 2) Rewrite asset refs in markdown
    md = archive_md_path.read_text(encoding="utf-8")
    if asset_map:
        def _sub(m: re.Match) -> str:
            prefix, ref, suffix = m.group(1), m.group(2), m.group(3)
            if ref in asset_map:
                return f"{prefix}{asset_map[ref]}{suffix}"
            return m.group(0)

        md = re.sub(r"(!\[[^\]]*\]\()([^)\s]+)([^)]*\))", _sub, md)

    # 3) Ensure all ancestor pages exist (ADO Wiki won't auto-create them)
    try:
        ensure_ancestor_pages(profile, token, page_path)
    except RuntimeError as e:
        raise RuntimeError(
            f"创建父页面失败：{e}\n"
            f"提示：请确认你对该 Wiki 有写权限，并且页面路径 '{page_path}' "
            f"中不含非法字符（如 ':' '?' '#' 等）。"
        ) from e

    # 4) PUT the page
    etag_before = get_page_etag(profile, token, page_path)
    code, body = put_page(profile, token, page_path, md)
    if code not in (200, 201):
        raise RuntimeError(
            f"上传 wiki 页面失败 (HTTP {code}):\n{body[:600]}"
        )

    page_url = (
        f"{_ui_base(profile)}?pagePath="
        f"{urllib.parse.quote(page_path, safe='')}"
    )
    return WikiResult(
        page_url=page_url,
        page_path=page_path,
        attachments_uploaded=n_attached,
        page_updated=etag_before is not None,
    )
