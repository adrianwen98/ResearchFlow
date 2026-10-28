"""集中管理配置；避免在代码中硬编码密钥。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_MANAGED_ENV: dict[str, str] = {}


def refresh_file_environment() -> None:
    """Reload file-owned values; keep explicitly inherited/overridden environment values."""
    values=dotenv_values(Path(os.getenv('WEBTASK_ENV_FILE',str(ROOT / '.env'))))
    for name, previous in list(_MANAGED_ENV.items()):
        if os.environ.get(name) != previous:
            _MANAGED_ENV.pop(name,None)
            continue
        value=values.get(name)
        if value is None:
            os.environ.pop(name,None)
            _MANAGED_ENV.pop(name,None)
        else:
            os.environ[name]=value
            _MANAGED_ENV[name]=value
    for name,value in values.items():
        if value is not None and name not in os.environ:
            os.environ[name]=value
            _MANAGED_ENV[name]=value


refresh_file_environment()


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    max_graph_steps: int
    max_pdf_bytes: int
    browser_headless: bool
    browser_name: str
    allowed_domains: tuple[str, ...]
    output_dir: Path
    checkpoint_path: Path
    max_model_calls: int = 30
    max_tool_calls: int = 90
    task_timeout: int = 420
    tool_timeout: int = 40
    model_timeout: float = 60
    mcp_version: str = "0.0.83"
    mcp_cli: str = ""
    network_mode: str = "public"
    public_dns_mode: str = "system"
    search_api_provider: str = ""
    search_api_key: str = ""

    @classmethod
    def load(cls) -> "Settings":
        refresh_file_environment()
        allowed = os.getenv("ALLOWED_DOMAINS", "")
        mode = os.getenv('NETWORK_MODE', 'public').strip().lower()
        dns_mode = os.getenv('PUBLIC_DNS_MODE', 'system').strip().lower()
        if mode not in {'public', 'strict'} or dns_mode not in {'system', 'cloudflare'}:
            raise ValueError('NETWORK_MODE 为 public/strict；PUBLIC_DNS_MODE 为 system/cloudflare')
        if mode == 'strict' and not allowed.strip():
            raise ValueError('strict 模式必须提供 ALLOWED_DOMAINS')
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip(),
            max_graph_steps=max(12, min(int(os.getenv("MAX_GRAPH_STEPS", "85")), 100)),
            max_pdf_bytes=max(1, min(int(os.getenv("MAX_PDF_MB", "8")), 30)) * 1024 * 1024,
            browser_headless=os.getenv("BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"},
            browser_name=os.getenv("BROWSER_NAME", "msedge" if os.name == "nt" else "chromium").strip(),
            allowed_domains=tuple(s.strip().lower().rstrip('.') for s in allowed.split(",") if s.strip()) if mode == 'strict' else (),
            output_dir=ROOT / "outputs",
            checkpoint_path=ROOT / "checkpoints.sqlite",
            max_model_calls=max(1, min(int(os.getenv("MAX_MODEL_CALLS", "30")), 50)),
            max_tool_calls=max(1, min(int(os.getenv("MAX_TOOL_CALLS", "90")), 150)),
            task_timeout=max(10,min(int(os.getenv("TASK_TIMEOUT_SECONDS", "420")),1800)),
            tool_timeout=max(1,min(int(os.getenv("TOOL_TIMEOUT_SECONDS", "40")),120)),
            model_timeout=max(5,min(float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),120)),
            mcp_version="0.0.83",
            mcp_cli=os.getenv("PLAYWRIGHT_MCP_CLI", ""),
            network_mode=mode,
            public_dns_mode=dns_mode,
            search_api_provider=os.getenv("SEARCH_API_PROVIDER", "").strip().lower(),
            search_api_key=os.getenv("SEARCH_API_KEY", "").strip(),
        )

    def validate(self) -> None:
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入自己的密钥。")
