from tomatocat.config import Config


def test_example_config_is_valid():
    config = Config.load("config.toml.example")
    assert config.validate() == []


def test_enabled_telegram_requires_token():
    config = Config()
    config.channels.telegram.enabled = True
    assert "channels.telegram.token is required when Telegram is enabled" in config.validate()


def test_invalid_cli_socket_is_reported():
    config = Config()
    config.channels.cli.socket = "localhost:not-a-port"
    assert "channels.cli.socket must be HOST:PORT with a valid port" in config.validate()
