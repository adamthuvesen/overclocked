from pathlib import Path

from overclocked.config import Config, load_config


def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]
    assert cfg.session_status is False
    assert cfg.session_metrics is False


def test_custom_redact_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text('[privacy]\nredact_paths = ["~/personal/", "~/work/"]\n')
    cfg = load_config()
    assert cfg.redact_paths == ["~/personal/", "~/work/"]


def test_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path / "nonexistent"))
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]


def test_malformed_toml_returns_defaults_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("this is [not valid toml !!!")
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()


def test_redact_paths_bare_string_returns_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[privacy]\nredact_paths = "~/clients/"\n')
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()


def test_redact_paths_list_of_ints_returns_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text("[privacy]\nredact_paths = [1, 2, 3]\n")
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()


def test_privacy_section_wrong_type_warns_and_uses_privacy_defaults(
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'privacy = "bad"\n[display]\nsession_status = true\n',
    )
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]
    assert cfg.session_status is True
    err = capsys.readouterr().err
    assert "privacy" in err
    assert "warning" in err.lower()


def test_display_section_wrong_type_warns_and_uses_display_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'display = []\n[privacy]\nredact_paths = ["~/work/"]\n',
    )
    cfg = load_config()
    assert cfg.redact_paths == ["~/work/"]
    assert cfg.session_status is False
    assert cfg.session_metrics is False
    assert cfg.show_subagents is True
    err = capsys.readouterr().err
    assert "display" in err
    assert "warning" in err.lower()


def test_is_redacted_matching_prefix(tmp_path):
    home = Path.home()
    cfg = Config(redact_paths=["~/clients/"])
    assert cfg.is_redacted(str(home / "clients" / "acme"))


def test_is_redacted_non_matching(tmp_path):
    cfg = Config(redact_paths=["~/clients/"])
    assert not cfg.is_redacted(str(Path.home() / "dev" / "myproject"))


def test_is_redacted_null_cwd():
    cfg = Config()
    assert not cfg.is_redacted(None)


def test_trailing_slash_normalised():
    cfg = Config(redact_paths=["~/clients"])
    home = Path.home()
    assert cfg.is_redacted(str(home / "clients" / "foo"))


def test_is_redacted_does_not_match_partial_path_segment():
    cfg = Config(redact_paths=["~/clients/"])
    home = Path.home()
    assert not cfg.is_redacted(str(home / "clients-archive" / "acme"))
    assert not cfg.is_redacted(str(home / "clients2" / "acme"))


def test_is_redacted_matches_exact_redaction_root():
    cfg = Config(redact_paths=["~/clients/"])
    assert cfg.is_redacted(str(Path.home() / "clients"))


def test_session_metrics_false(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[privacy]\nredact_paths = ["~/clients/"]\n[display]\nsession_metrics = false\n',
    )
    cfg = load_config()
    assert cfg.session_metrics is False


def test_session_metrics_invalid_type_warns_and_defaults_false(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[privacy]\nredact_paths = ["~/clients/"]\n[display]\nsession_metrics = "no"\n',
    )
    cfg = load_config()
    assert cfg.session_metrics is False
    assert "session_metrics" in capsys.readouterr().err


def test_session_status_true(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "[display]\nsession_status = true\n",
    )
    cfg = load_config()
    assert cfg.session_status is True
    assert cfg.session_metrics is False


def test_session_status_invalid_type_warns_and_defaults_false(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[display]\nsession_status = "yes"\n',
    )
    cfg = load_config()
    assert cfg.session_status is False
    assert "session_status" in capsys.readouterr().err
