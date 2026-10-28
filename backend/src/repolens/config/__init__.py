"""统一读取本地运行配置；配置文件不包含凭据。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"


@dataclass(frozen=True)
class LimitsConfig:
    browse_files: int = 5_000
    browse_file_bytes: int = 1_000_000
    browse_bytes: int = 64_000_000
    analysis_files: int = 200
    analysis_file_bytes: int = 512_000
    analysis_bytes: int = 8_000_000
    directories: int = 10_000


@dataclass(frozen=True)
class AgentConfig:
    max_rounds: int = 20
    max_repairs: int = 2
    max_read_lines: int = 200
    max_output_tokens: int = 8_192
    context_chars: int = 80_000
    request_timeout_seconds: int = 60
    wall_time_seconds: int = 180


@dataclass(frozen=True)
class ServerConfig:
    log_dir: Path = PROJECT_ROOT / "logs"
    port: int = 0
    open_browser: bool = False


@dataclass(frozen=True)
class AppConfig:
    path: Path = DEFAULT_CONFIG_PATH
    limits: LimitsConfig = LimitsConfig()
    agent: AgentConfig = AgentConfig()
    server: ServerConfig = ServerConfig()
    warnings: tuple[str, ...] = ()


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    return value if isinstance(value, dict) else {}


def _integer(section: dict, name: str, default: int, minimum: int, maximum: int,
            warnings: list[str], label: str) -> int:
    value = section.get(name, default)
    if type(value) is int and minimum <= value <= maximum:
        return value
    warnings.append(f"{label} 无效，已使用默认值 {default}。")
    return default


def _boolean(section: dict, name: str, default: bool, warnings: list[str], label: str) -> bool:
    value = section.get(name, default)
    if type(value) is bool:
        return value
    warnings.append(f"{label} 无效，已使用默认值 {str(default).lower()}。")
    return default


def _log_path(value: object, warnings: list[str]) -> Path:
    if value is None:
        return PROJECT_ROOT / "logs"
    if not isinstance(value, str) or not value.strip():
        warnings.append("server.log_dir 无效，已使用默认目录 logs。")
        return PROJECT_ROOT / "logs"
    expanded = Path(os.path.expandvars(value)).expanduser()
    return expanded if expanded.is_absolute() else PROJECT_ROOT / expanded


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load config.toml on each process start, falling back safely on errors."""
    configured_path = path or os.environ.get("REPOLENS_CONFIG") or DEFAULT_CONFIG_PATH
    config_path = Path(configured_path).expanduser()
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    warnings: list[str] = []
    raw: dict = {}
    if config_path.exists():
        try:
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("root must be a table")
            raw = parsed
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError):
            warnings.append(f"无法读取配置文件 {config_path.name}，已使用默认配置。")

    limits = _section(raw, "limits")
    limit_config = LimitsConfig(
        browse_files=_integer(limits, "browse_files", 5_000, 100, 100_000, warnings, "limits.browse_files"),
        browse_file_bytes=_integer(limits, "browse_file_bytes", 1_000_000, 16_384, 10_000_000, warnings, "limits.browse_file_bytes"),
        browse_bytes=_integer(limits, "browse_bytes", 64_000_000, 1_000_000, 1_000_000_000, warnings, "limits.browse_bytes"),
        analysis_files=_integer(limits, "analysis_files", 200, 1, 10_000, warnings, "limits.analysis_files"),
        analysis_file_bytes=_integer(limits, "analysis_file_bytes", 512_000, 16_384, 5_000_000, warnings, "limits.analysis_file_bytes"),
        analysis_bytes=_integer(limits, "analysis_bytes", 8_000_000, 1_000_000, 100_000_000, warnings, "limits.analysis_bytes"),
        directories=_integer(limits, "directories", 10_000, 100, 100_000, warnings, "limits.directories"),
    )

    agent = _section(raw, "agent")
    for retired in ("token_budget", "max_tool_calls"):
        if retired in agent:
            warnings.append(f"agent.{retired} 已停用，累计 token 与工具调用次数仅统计，不作为停止条件。")
    agent_config = AgentConfig(
        max_rounds=_integer(agent, "max_rounds", 20, 1, 100, warnings, "agent.max_rounds"),
        max_repairs=_integer(agent, "max_repairs", 2, 0, 5, warnings, "agent.max_repairs"),
        max_read_lines=_integer(agent, "max_read_lines", 200, 20, 1_000, warnings, "agent.max_read_lines"),
        max_output_tokens=_integer(agent, "max_output_tokens", 8_192, 256, 32_000, warnings, "agent.max_output_tokens"),
        context_chars=_integer(agent, "context_chars", 80_000, 1_000, 500_000, warnings, "agent.context_chars"),
        request_timeout_seconds=_integer(agent, "request_timeout_seconds", 60, 5, 600, warnings, "agent.request_timeout_seconds"),
        wall_time_seconds=_integer(agent, "wall_time_seconds", 180, 10, 3_600, warnings, "agent.wall_time_seconds"),
    )

    server = _section(raw, "server")
    server_config = ServerConfig(
        log_dir=_log_path(server.get("log_dir"), warnings),
        port=_integer(server, "port", 0, 0, 65_535, warnings, "server.port"),
        open_browser=_boolean(server, "open_browser", False, warnings, "server.open_browser"),
    )
    return AppConfig(config_path, limit_config, agent_config, server_config, tuple(warnings))


CONFIG = load_config()


def get_config() -> AppConfig:
    return CONFIG
