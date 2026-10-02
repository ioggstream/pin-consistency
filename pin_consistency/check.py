"""Check that container images and GitHub Actions are sha-pinned consistently."""

import argparse
import json
import os
import re
import shutil
import subprocess  # noqa: S404  fixed argv, no shell
import sys
from collections import defaultdict
from typing import NamedTuple
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

NOQA = "noqa: pin-consistency"
SHA1 = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
DIGEST_REF = re.compile(r"[\w./:-]+@sha256:[0-9a-f]{64}")
FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.I)
COPY_RE = re.compile(r"^\s*COPY\b.*?--from=(\S+)", re.I)
KEY_RE = re.compile(r"^\s*(?:-\s+)?([\w-]+)\s*:\s*(.*)$")
SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9._:/-]*", re.I)
SAFE_TAG = re.compile(r"\w[\w.+-]{0,127}")
ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
TIMEOUT = 15


class Ref(NamedTuple):
    kind: str  # image, action, hook, tool
    name: str
    version: str | None
    pin: str | None
    file: str
    line: int
    noqa: bool = False
    arg: bool = False


def comment_version(comment):
    tokens = [t for t in comment.replace(NOQA, "").split() if t != "#"]
    return tokens[0] if tokens else None


def parse_image(ref):
    """Return (name, tag, digest) of an image reference."""
    ref = ref.removeprefix("docker://")
    ref, _, digest = ref.partition("@")
    head, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        head, tag = ref, None
    for prefix in ("docker.io/", "index.docker.io/", "library/"):
        head = head.removeprefix(prefix)
    return head, tag, digest if DIGEST.fullmatch(digest) else None


def image_ref(value, comment, file, line, noqa):
    name, tag, digest = parse_image(value)
    version = tag or comment_version(comment)
    return Ref("image", name, version, digest, file, line, noqa, "$" in value)


def read_lines(path):
    """Yield (number, line, noqa); a noqa comment line also covers the next line."""
    prev = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            noqa = NOQA in line
            yield n, line.rstrip("\n"), noqa or prev
            prev = noqa and line.lstrip().startswith("#")


def dockerfile_refs(path, lines):
    stages = set()
    for n, line, noqa in lines:
        if m := FROM_RE.match(line):
            image, stage = m.groups()
        elif m := COPY_RE.match(line):
            image, stage = m.group(1), None
        else:
            continue
        if image.lower() not in stages | {"scratch"} and not image.isdigit():
            yield image_ref(image, "", path, n, noqa)
        if stage:
            stages.add(stage.lower())


def hook_name(url):
    return re.sub(r"^[a-z+]+://", "", url).removesuffix("/").removesuffix(".git")


def yaml_refs(path, lines):
    repo = uses = None
    for n, line, noqa in lines:
        if line.lstrip().startswith("#"):
            continue
        key, raw = m.groups() if (m := KEY_RE.match(line)) else ("", line)
        value, _, comment = raw.partition(" #")
        value = value.strip().strip("'\"")
        if key == "repo":
            repo = value if "://" in value else None
        elif key == "rev" and repo:
            pin = value if SHA1.fullmatch(value) else None
            version = comment_version(comment) or (None if pin else value)
            yield Ref("hook", hook_name(repo), version, pin, path, n, noqa)
        elif key == "uses" and value.startswith("docker://"):
            yield image_ref(value, comment, path, n, noqa)
        elif key == "uses" and value and not value.startswith("./"):
            action, _, ref = value.partition("@")
            uses = "/".join(action.split("/")[:2])
            pin = ref if SHA1.fullmatch(ref) else None
            version = comment_version(comment) or (None if pin else ref or None)
            yield Ref("action", uses, version, pin, path, n, noqa, "$" in value)
        elif key in ("image", "container") and value and value[0] not in "{[|>":
            yield image_ref(value, comment, path, n, noqa)
        elif key == "version" and uses and "@" in value:
            version, _, digest = value.partition("@")
            pin = digest if DIGEST.fullmatch(digest) else None
            yield Ref("tool", uses, version, pin, path, n, noqa)
        else:
            for image in DIGEST_REF.findall(raw):
                yield image_ref(image, comment, path, n, noqa)


def extract(path):
    base = os.path.basename(path)
    if os.path.islink(path) or not os.path.isfile(path):
        return []
    if "Dockerfile" in base or "Containerfile" in base or base.endswith(".dockerfile"):
        return list(dockerfile_refs(path, read_lines(path)))
    if base.endswith((".yml", ".yaml")):
        return list(yaml_refs(path, read_lines(path)))
    return []


def check(refs):
    """Return (level, rule, ref) findings."""
    findings = []
    by_version, by_name = defaultdict(set), defaultdict(set)
    for r in refs:
        if r.arg:
            findings.append(("warning", "arg-substituted", r))
            continue
        if not r.pin:
            findings.append(("error", "unpinned", r))
        if r.noqa:
            continue
        if r.version:
            by_name[r.kind, r.name].add(r.version)
            if r.pin:
                by_version[r.kind, r.name, r.version].add(r.pin)
        elif r.pin:
            findings.append(("warning", "comment-missing", r))
    for r in refs:
        if r.arg or r.noqa or not r.version:
            continue
        if r.pin and len(by_version[r.kind, r.name, r.version]) > 1:
            findings.append(("error", "digest-mismatch", r))
        if len(by_name[r.kind, r.name]) > 1:
            findings.append(("warning", "tag-spread", r))
    return findings


def https_open(req):
    url = req.full_url if isinstance(req, Request) else req
    if urlsplit(url).scheme != "https":
        raise ValueError(f"refusing non-https url: {url}")
    return urlopen(req, timeout=TIMEOUT)  # noqa: S310  scheme checked above


def registry_digest(name, tag):
    """Resolve an image tag to its manifest digest via the registry v2 API."""
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first):
        host, repo = first, rest
    else:
        host, repo = "registry-1.docker.io", name if rest else f"library/{name}"
    req = Request(f"https://{host}/v2/{repo}/manifests/{tag}", method="HEAD")
    req.add_header("Accept", ACCEPT)
    try:
        return https_open(req).headers["Docker-Content-Digest"]
    except HTTPError as e:
        if e.code != 401:
            raise
        challenge = dict(
            re.findall(r'(\w+)="([^"]*)"', e.headers.get("WWW-Authenticate", ""))
        )
    realm = challenge.pop("realm", "")
    with https_open(f"{realm}?{urlencode(challenge)}") as resp:
        body = json.load(resp)
    req.add_header(
        "Authorization", f"Bearer {body.get('token') or body['access_token']}"
    )
    return https_open(req).headers["Docker-Content-Digest"]


def git_tag_sha(url, tag):
    """Resolve a git tag to its commit sha, peeling annotated tags."""
    ref = f"refs/tags/{tag}"
    out = subprocess.run(  # noqa: S603  fixed argv, validated inputs
        [shutil.which("git") or "git", "ls-remote", url, ref, ref + "^{}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT * 2,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    ).stdout
    shas = {r: sha for sha, r in (line.split("\t") for line in out.splitlines())}
    return shas.get(ref + "^{}") or shas[ref]


def resolve(kind, name, version):
    if not SAFE_NAME.fullmatch(name) or not SAFE_TAG.fullmatch(version):
        raise ValueError("unsafe reference")
    if kind == "image":
        return registry_digest(name, version)
    if kind == "action":
        return git_tag_sha(f"https://github.com/{name}", version)
    if kind == "hook":
        return git_tag_sha(f"https://{name}", version)
    return None


def verify(refs):
    """Return findings for pins that differ from what their tag resolves to."""
    findings, cache = [], {}
    for r in refs:
        if r.arg or r.noqa or not (r.pin and r.version):
            continue
        key = (r.kind, r.name, r.version)
        if key not in cache:
            try:
                cache[key] = resolve(*key)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
                cache[key] = e
        got = cache[key]
        if isinstance(got, Exception):
            findings.append(("error", f"verify-failed ({got})", r))
        elif got and got != r.pin:
            findings.append(("error", f"tag-moved ({got})", r))
    return findings


def git_files():
    out = subprocess.run(  # noqa: S603  fixed argv
        [shutil.which("git") or "git", "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files", nargs="*", help="files to scan (default: git ls-files)"
    )
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as errors"
    )
    parser.add_argument("--verify", action="store_true", help="resolve tags online")
    args = parser.parse_args(argv)

    refs = [r for path in args.files or git_files() for r in extract(path)]
    findings = check(refs) + (verify(refs) if args.verify else [])
    findings.sort(key=lambda f: (f[2].file, f[2].line))
    for level, rule, r in findings:
        print(
            f"{r.file}:{r.line}: {level}: {rule}: {r.kind} {r.name} {r.version or '-'} {r.pin or '-'}"
        )
    failing = {"error", "warning"} if args.strict else {"error"}
    return int(any(level in failing for level, _, _ in findings))


if __name__ == "__main__":
    sys.exit(main())
