from pathlib import Path

from overclocked.config import Config, load_config


def test_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    cfg = load_config()
    assert cfg.redact_paths == ["~/clients/"]


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
