from pathlib import Path

from overclocked.config import Config
from overclocked.identity import _parse_lsof_fn_cwd_blocks, project_label, session_key


def test_parse_lsof_fn_cwd_blocks_multi_pid():
    text = """p1000
fcwd
n/tmp/a
p2000
fcwd
n/tmp/b
"""
    m = _parse_lsof_fn_cwd_blocks(text)
    assert m[1000] == "/tmp/a"
    assert m[2000] == "/tmp/b"


def test_project_label_matching_prefix(tmp_path):
    home = Path.home()
    cfg = Config(redact_paths=["~/clients/"])
    cwd = str(home / "clients" / "acme" / "webapp")
    assert project_label(cwd, cfg) == "redacted"


def test_project_label_non_matching():
    cfg = Config(redact_paths=["~/clients/"])
    cwd = str(Path.home() / "dev" / "overclocked")
    assert project_label(cwd, cfg) == "overclocked"


def test_project_label_null_cwd():
    cfg = Config()
    assert project_label(None, cfg) is None


def test_project_label_multiple_prefixes():
    cfg = Config(redact_paths=["~/clients/", "~/personal/"])
    home = Path.home()
    assert project_label(str(home / "personal" / "diary"), cfg) == "redacted"
    assert project_label(str(home / "dev" / "work"), cfg) == "work"


def test_project_label_claude_worktree_uses_repo_name():
    cfg = Config()
    cwd = str(
        Path.home()
        / "dev"
        / "my-project"
        / ".claude"
        / "worktrees"
        / "thirsty-lichterman-918d5e"
    )
    assert project_label(cwd, cfg) == "my-project"


def test_project_label_codex_worktree_uses_repo_name():
    cfg = Config()
    cwd = str(Path.home() / "dev" / "my-other-project" / ".codex" / "worktrees" / "abc123")
    assert project_label(cwd, cfg) == "my-other-project"


def test_session_key_with_cwd():
    key = session_key("claude", "/Users/adam/dev/overclocked", 12345)
    assert key == "claude:/Users/adam/dev/overclocked"
    assert "12345" not in key


def test_session_key_without_cwd():
    key = session_key("codex", None, 42)
    assert key == "codex:pid:42"
