"""Lightweight customer-data redaction for archive output.

Goal: scrub the most common PII / customer identifiers from text that
might end up in shared markdown reports.

Patterns redacted (with stable mapping so repeated occurrences become
the same placeholder):

* GUIDs (subscription/tenant/object/principal IDs)         → <GUID-NNN>
* Email / UPN addresses                                    → <EMAIL-NNN>
* IPv4 addresses (excluding obvious examples like 0.0.0.0) → <IP-NNN>
* Resource Group names inside ARM resource IDs             → <RG-NNN>
* Resource leaf names inside ARM resource IDs              → <RES-NNN>
* AccountKey=..., SharedAccessSignature=..., SAS tokens    → <SECRET-REDACTED>
* Bearer tokens                                            → <TOKEN-REDACTED>
* Phone numbers (international form)                       → <PHONE-NNN>

The redactor returns the redacted text plus a mapping table of
``placeholder -> original`` (kept locally next to archive.md as
``redact_map.json`` so the user can de-redact if needed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- patterns --------------------------------------------------------

_GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)
_PHONE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[ \-]?)?(?:\(?\d{2,4}\)?[ \-]?){2,4}\d{2,4}(?!\w)"
)
# Inside an ARM resource ID: /resourceGroups/<name>/providers/...
_RG_IN_ARM = re.compile(
    r"(/resourceGroups/)([^/\s]+)", re.IGNORECASE
)
# Resource name after providers/<rp>/<type>/<name>(/...optional sub)
# Example: /providers/Microsoft.ContainerService/managedClusters/aks-foo
_RES_IN_ARM = re.compile(
    r"(/providers/[^/\s]+/[^/\s]+/)([^/\s]+)", re.IGNORECASE
)
_ACCOUNT_KEY = re.compile(
    r"(AccountKey|SharedAccessSignature|sig|SharedKey)\s*=\s*[A-Za-z0-9+/=%_\-]+",
    re.IGNORECASE,
)
_BEARER = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE
)
_SAS_QS = re.compile(
    r"\?[a-zA-Z0-9_\-=&%]*sig=[A-Za-z0-9%_\-+/=]+[a-zA-Z0-9_\-=&%]*"
)

# Allow-list: never redact these well-known values
_GUID_ALLOWLIST = {
    "00000000-0000-0000-0000-000000000000",
}
_EMAIL_ALLOWLIST = {
    "noreply@microsoft.com",
}
_IP_ALLOWLIST = {
    "0.0.0.0", "127.0.0.1", "255.255.255.255", "169.254.169.254",
}


@dataclass
class Redactor:
    """Stateful redactor that keeps a stable name map across many
    ``redact()`` calls so that the same secret always becomes the same
    placeholder."""

    counters: dict[str, int] = field(default_factory=dict)
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> orig
    reverse: dict[str, str] = field(default_factory=dict)  # orig -> placeholder

    def _placeholder(self, kind: str, original: str) -> str:
        if original in self.reverse:
            return self.reverse[original]
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        ph = f"<{kind}-{n:03d}>"
        self.mapping[ph] = original
        self.reverse[original] = ph
        return ph

    # --- per-pattern handlers -----------------------------------------

    def _sub_guid(self, m: re.Match) -> str:
        v = m.group(0)
        if v.lower() in _GUID_ALLOWLIST:
            return v
        return self._placeholder("GUID", v)

    def _sub_email(self, m: re.Match) -> str:
        v = m.group(0)
        if v.lower() in _EMAIL_ALLOWLIST:
            return v
        return self._placeholder("EMAIL", v)

    def _sub_ip(self, m: re.Match) -> str:
        v = m.group(0)
        if v in _IP_ALLOWLIST:
            return v
        # Skip version-like sequences (e.g., 1.2.3.4 inside 1.2.3.4.5)
        return self._placeholder("IP", v)

    def _sub_rg(self, m: re.Match) -> str:
        prefix, name = m.group(1), m.group(2)
        return f"{prefix}{self._placeholder('RG', name)}"

    def _sub_res(self, m: re.Match) -> str:
        prefix, name = m.group(1), m.group(2)
        return f"{prefix}{self._placeholder('RES', name)}"

    def _sub_phone(self, m: re.Match) -> str:
        v = m.group(0)
        digits = re.sub(r"\D", "", v)
        if len(digits) < 7 or len(digits) > 15:
            return v
        return self._placeholder("PHONE", v)

    # --- public --------------------------------------------------------

    def redact(self, text: str) -> str:
        if not text:
            return text
        # Order matters: secrets first (raw), then resource-id specifics
        # (because they need to run before plain GUIDs to keep RG/RES
        # named via path context), then GUIDs, emails, IPs, phones.
        text = _ACCOUNT_KEY.sub(r"\1=<SECRET-REDACTED>", text)
        text = _SAS_QS.sub("?<SAS-REDACTED>", text)
        text = _BEARER.sub(r"\1<TOKEN-REDACTED>", text)
        text = _RG_IN_ARM.sub(self._sub_rg, text)
        text = _RES_IN_ARM.sub(self._sub_res, text)
        text = _GUID.sub(self._sub_guid, text)
        text = _EMAIL.sub(self._sub_email, text)
        text = _IPV4.sub(self._sub_ip, text)
        text = _PHONE.sub(self._sub_phone, text)
        return text
