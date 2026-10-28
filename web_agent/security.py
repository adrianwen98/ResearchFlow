"""防止通过工具访问本地文件/局域网；输出文件名、表格内容安全处理。"""
from __future__ import annotations

import ipaddress
import re
import socket
import os
from urllib.parse import urlsplit


class UnsafeURL(ValueError):
    pass


def binary_document_url(url):
    parsed=urlsplit(url)
    # GitHub blob URLs render an HTML file viewer despite the file suffix.
    html_viewer=parsed.hostname=='github.com' and '/blob/' in parsed.path
    return not html_viewer and parsed.path.lower().endswith(('.xlsx','.pdf'))


def image_document_url(url):
    """Images cannot be turned into text by the current browser tools (no OCR)."""
    parsed=urlsplit(url)
    return parsed.path.lower().endswith(
        ('.jpg','.jpeg','.png','.gif','.webp','.bmp','.tif','.tiff','.svg'))


def validate_public_url(url: str, allowed_domains: tuple[str, ...] = ()) -> str:
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"https", "http"}:
        raise UnsafeURL("仅允许 http/https 网址，禁止本地文件等协议")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeURL("网址必须有公共域名且不能包含账号密码")
    host = parsed.hostname.lower().rstrip(".")
    if '%' in host or '\\' in value or any(ord(c) < 33 for c in value):
        raise UnsafeURL('网址包含歧义字符或 IPv6 区域标识')
    if parsed.port not in {None, 80, 443}:
        raise UnsafeURL("仅允许 80/443 公开网页端口")
    if re.fullmatch(r"[0-9.]+|0x[0-9a-f]+", host):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise UnsafeURL("禁止非标准数字 IP 地址")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".localhost", ".local", ".internal", ".test", ".invalid")
    ):
        raise UnsafeURL("禁止访问本机、内网或保留域名")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise UnsafeURL("禁止访问不带顶级域名的主机")
    else:
        from .egress import public_ip
        if not public_ip(str(ip)):
            raise UnsafeURL("禁止访问私网、环回或非公网 IP")
    if allowed_domains and not any(host == d or host.endswith("." + d) for d in allowed_domains):
        raise UnsafeURL(f"目标域名 {host} 不在 ALLOWED_DOMAINS 白名单中")
    return value


def validate_resolved_url(url: str, allowed_domains: tuple[str, ...] = ()) -> str:
    """Preflight DNS for HTTP file tools. Redirects remain disabled."""
    checked = validate_public_url(url, allowed_domains)
    addresses = socket.getaddrinfo(urlsplit(checked).hostname, 443, type=socket.SOCK_STREAM)
    def public(address):
        ip = ipaddress.ip_address(address)
        from .egress import public_ip
        return public_ip(str(ip))
    if not addresses or any(not public(item[4][0]) for item in addresses):
        raise UnsafeURL("DNS 指向非公网地址，拒绝访问")
    return checked


def safe_identifier(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:56]
    if not safe:
        raise ValueError("任务 ID 不能为空")
    return safe


def excel_safe(value: object) -> str:
    """阻止恶意网页文本被 Excel 当成公式执行。"""
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


RISKY_CLICK_WORDS = (
    "delete", "remove", "purchase", "checkout", "pay", "confirm payment", "send email",
    "publish", "submit order", "submit form", "sign up", "register", "login", "log in",
    "删除", "支付", "购买", "下单", "付款", "发布", "注册", "提交订单", "发送邮件",
)


def check_readonly_click(element: str) -> None:
    label = (element or "").lower()
    if any(word in label for word in RISKY_CLICK_WORDS):
        raise PermissionError("演示版禁止执行具有副作用的网页操作：" + element[:100])
