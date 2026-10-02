from pathlib import Path

import pytest

from pin_consistency.check import check, extract, main

RESOURCES = Path(__file__).parent / "resources"

SHA = "a" * 40
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


@pytest.fixture
def scan(tmp_path):
    """Copy tests/resources/<resource> to <tmp>/<target>, then scan the copies."""

    def _scan(**files):
        paths = []
        for target, resource in files.items():
            path = tmp_path / target.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text((RESOURCES / resource).read_text())
            paths.append(str(path))
        refs = [r for p in paths for r in extract(p)]
        return refs, {(rule, r.kind, r.name) for _, rule, r in check(refs)}

    return _scan


def test_dockerfile_from(scan):
    refs, found = scan(Dockerfile="Dockerfile")
    assert [(r.name, r.version, r.pin) for r in refs] == [
        ("python", "3.13", D1),
        ("busybox", "1", None),
    ]
    assert found == {("unpinned", "image", "busybox")}


def test_dockerfile_arg(scan):
    _, found = scan(Dockerfile="Dockerfile.arg")
    assert found == {("arg-substituted", "image", "${BASE}")}


def test_workflow_forms(scan):
    refs, found = scan(**{"w.yml": "gh-action.yaml"})
    assert {(r.kind, r.name, r.version, r.pin) for r in refs} == {
        ("action", "actions/checkout", "v4.1.0", SHA),
        ("image", "alpine", "3.20", D1),
        ("action", "org/tool", "v1", SHA),
        ("tool", "org/tool", "3.93.3", D2),
        ("image", "python", "3.9", None),
        ("image", "ghcr.io/o/i", "1.0", D1),
    }
    assert found == {("unpinned", "image", "python")}


def test_unpinned_action(scan):
    _, found = scan(**{"w.yml": "gh-action-unpinned.yaml"})
    assert found == {("unpinned", "action", "actions/checkout")}


def test_pre_commit_config(scan):
    refs, found = scan(**{".pre-commit-config.yaml": "pre-commit-config.yaml"})
    assert {(r.kind, r.name, r.version, r.pin) for r in refs} == {
        ("hook", "github.com/o/r", "v1.0.0", SHA),
        ("hook", "github.com/o/s", "v2", None),
        ("image", "alpine", "3.20", D1),
    }
    assert found == {("unpinned", "hook", "github.com/o/s")}


def test_compose_image(scan):
    _, found = scan(**{"docker-compose.yaml": "docker-compose.yaml"})
    assert found == {("unpinned", "image", "redis")}


def test_digest_mismatch(scan):
    _, found = scan(
        Dockerfile="Dockerfile.mismatch",
        **{"w.yml": "gh-image-mismatch.yaml"},
    )
    assert found == {("digest-mismatch", "image", "python")}


def test_tag_spread(scan):
    _, found = scan(
        Dockerfile="Dockerfile.spread",
        **{"w.yml": "gh-image-spread.yaml"},
    )
    assert found == {("tag-spread", "image", "python")}


def test_comment_missing(scan):
    _, found = scan(**{"w.yml": "gh-action-nocomment.yaml"})
    assert found == {("comment-missing", "action", "actions/checkout")}


def test_noqa(scan):
    _, found = scan(
        Dockerfile="Dockerfile.noqa",
        **{"w.yml": "gh-image-noqa.yaml"},
    )
    assert found == set()


def test_noqa_keeps_unpinned(scan):
    _, found = scan(**{"w.yml": "gh-image-noqa-unpinned.yaml"})
    assert found == {("unpinned", "image", "redis")}


def test_main_exit_codes(capsys):
    ok = RESOURCES / "gh-action-ok.yaml"
    bad = RESOURCES / "gh-action-bad.yaml"
    assert main([str(ok)]) == 0
    assert main([str(ok), "--strict"]) == 1
    assert main([str(bad)]) == 1
    assert "unpinned" in capsys.readouterr().out
