# Evidence redaction (shared; generated from scripts/kaggle_cells/evidence.py). Defence in depth: credentials are never recorded on purpose, and every
# MARK line and every exported text file is still scrubbed of anything shaped like an Enigma runtime key, a diagnostic key, an AWS key id, a bearer
# token or a JWT before it is written or archived.
import re, pathlib, zipfile
_SECRET_RE = re.compile(r"(?:enig_rt_|edx_)[a-z0-9]{8}_[A-Za-z0-9_-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
                        r"|\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*")
_EXPORT_SKIP_DIRS = ("enigma-src",)                    # the public connector clone is not evidence
_EXPORT_SKIP_SUFFIXES = (".whl",)                      # the public 38 MB wheel is not evidence (its sha256 is in the markers)


def redact(text):
    return _SECRET_RE.sub("<REDACTED>", text)


def write_redacted_archive(src_dir, zip_path):
    """Zip src_dir, scrubbing every UTF-8 text file. Returns (files, redactions)."""
    files = hits = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pathlib.Path(src_dir).rglob("*")):
            rel = p.relative_to(src_dir)
            if not p.is_file() or p.is_symlink() or rel.parts[0] in _EXPORT_SKIP_DIRS or p.suffix in _EXPORT_SKIP_SUFFIXES:
                continue
            data = p.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                z.writestr(str(rel), data)
            else:
                hits += len(_SECRET_RE.findall(text))
                z.writestr(str(rel), redact(text))
            files += 1
    return files, hits
