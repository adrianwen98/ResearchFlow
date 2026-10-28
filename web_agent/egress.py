"""Ephemeral public-only browser/file egress. Resolve once, connect to that IP.

This is an application egress boundary, not an OS sandbox for a compromised browser.
No environment proxy, insecure TLS, Fake-IP allowance or private DNS exception.
"""
from __future__ import annotations

import asyncio
import ipaddress
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import httpx

from .security import UnsafeURL, validate_public_url


def public_ip(value):
    ip = ipaddress.ip_address(value)
    # Reject transition/translation addresses which can encode private IPv4.
    if ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo or
                           ip in ipaddress.ip_network('64:ff9b::/96') or
                           ip in ipaddress.ip_network('64:ff9b:1::/48')):
        return False
    if ip.version==4 and (ip in ipaddress.ip_network('192.0.0.0/24') or ip in ipaddress.ip_network('192.88.99.0/24')):return False
    return ip.is_global and not ip.is_multicast


async def resolve_public(host, mode='system'):
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if mode == 'cloudflare':
            # Fixed public bootstrap IP + normal TLS verification. Never use the
            # possibly synthetic system resolver to locate the resolver itself.
            async with httpx.AsyncClient(trust_env=False, timeout=8) as client:
                answers = await asyncio.gather(*[
                    client.get('https://1.1.1.1/dns-query', params={'name': host, 'type': kind},
                               headers={'accept': 'application/dns-json'}) for kind in ('A', 'AAAA')])
            addresses = []
            for response in answers:
                response.raise_for_status()
                payload = response.json()
                if payload.get('Status') != 0:
                    raise OSError('公共 DNS 解析失败')
                addresses.extend(item['data'] for item in payload.get('Answer', []) if item.get('type') in (1, 28))
        elif mode == 'system':
            rows = await asyncio.get_running_loop().getaddrinfo(host, 443, type=__import__('socket').SOCK_STREAM)
            addresses = [item[4][0] for item in rows]
        else:
            raise ValueError('PUBLIC_DNS_MODE 只能为 system/cloudflare')
    addresses = list(dict.fromkeys(addresses))
    if not addresses or any(not public_ip(address) for address in addresses):
        raise UnsafeURL('DNS 包含非公网/地址转换目标，拒绝连接；Fake-IP 不再被当成公网')
    return addresses


class PublicEgress:
    """One local HTTP CONNECT proxy per MCP session; no persistent daemon."""
    def __init__(self, *, dns_mode='system', log=None,allowed_domains=()):
        self.dns_mode, self.log = dns_mode, log
        self.allowed_domains=allowed_domains
        self.server = None
        self.tasks = set()
        self.connections = set()
        self.last_denial = None

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._serve, '127.0.0.1', 0, limit=32768)
        self.url = 'http://127.0.0.1:' + str(self.server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *args):
        self.server.close()
        for writer in list(self.connections):
            writer.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.server.wait_closed()

    async def connect(self, host, port):
        authority='['+host+']' if ':' in host else host
        validate_public_url('https://'+authority,self.allowed_domains)
        addresses = await resolve_public(host, self.dns_mode)
        last = None
        for address in addresses:
            try:
                # Numeric address: no second DNS resolution by the socket layer.
                reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), 8)
                peer = writer.get_extra_info('peername')[0]
                if not public_ip(peer) or ipaddress.ip_address(peer) != ipaddress.ip_address(address):
                    writer.close()
                    raise UnsafeURL('连接的实际地址与固定公网地址不一致')
                return reader, writer
            except OSError as exc:
                last = exc
        raise last or ConnectionError('无法连接已验证的公网地址')

    async def _pipe(self, reader, writer):
        try:
            while chunk := await asyncio.wait_for(reader.read(65536), 90):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task); self.connections.add(writer)
        upstream = None
        target = ''
        try:
            if len(self.tasks) > 64:
                raise OSError('出口连接数达到上限')
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            line, *lines = header.decode('latin1').split('\r\n')
            method, target, version = line.split(' ')
            if version not in {'HTTP/1.0', 'HTTP/1.1'}:
                raise UnsafeURL('出口协议不支持')
            if method == 'CONNECT':
                url = validate_public_url('https://' + target)
                parsed = urlsplit(url)
                if parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443):
                    raise UnsafeURL('CONNECT 仅支持标准 HTTPS 端口')
                first = None
            else:
                url = validate_public_url(target)
                parsed = urlsplit(url)
                if parsed.scheme != 'http' or method not in {'GET', 'HEAD', 'POST'}:
                    raise UnsafeURL('出口仅允许 HTTP 读取和审批后的 POST')
                # Strip proxy-only headers and force close: no second absolute
                # request can escape validation on the same cleartext socket.
                filtered = [h for h in lines if h and h.split(':', 1)[0].lower() not in
                            {'proxy-authorization', 'proxy-connection', 'connection', 'host'}]
                names = [h.split(':', 1)[0].lower() for h in filtered]
                if names.count('content-length') > 1 or ('transfer-encoding' in names and 'content-length' in names):
                    raise UnsafeURL('拒绝歧义请求体')
                path = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
                first = (f'{method} {path} HTTP/1.1\r\nHost: {parsed.netloc}\r\n' +
                         '\r\n'.join(filtered) + '\r\nConnection: close\r\n\r\n').encode('latin1')
            remote, upstream = await self.connect(parsed.hostname, parsed.port or (443 if method == 'CONNECT' else 80))
            self.connections.add(upstream)
            if first:
                upstream.write(first); await upstream.drain()
            else:
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n'); await writer.drain()
            await asyncio.gather(self._pipe(reader, upstream), self._pipe(remote, writer))
        except UnsafeURL as exc:
            self.last_denial = {'code': 'permission_denied', 'message': str(exc), 'target': target}
            if self.log:
                self.log('network_denied', str(exc), target=target)
            if not writer.is_closing():writer.write(b'HTTP/1.1 403 Forbidden\r\nX-WebTask-Blocked: 1\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, httpx.HTTPError) as exc:
            # DNS-over-HTTPS can raise httpx.ReadTimeout (not built-in
            # TimeoutError). A proxy callback must never leak that exception
            # into asyncio's unhandled client_connected_cb log.
            if self.log:
                self.log('network_error', type(exc).__name__, error_type=type(exc).__name__)
            if not writer.is_closing():
                try:
                    writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
                    await asyncio.wait_for(writer.drain(), 1)
                except (OSError, TimeoutError):
                    pass
        finally:
            writer.close(); self.connections.discard(writer)
            if upstream:
                upstream.close(); self.connections.discard(upstream)
            self.tasks.discard(task)


@asynccontextmanager
async def public_http_client(settings):
    async with PublicEgress(dns_mode=settings.public_dns_mode,allowed_domains=settings.allowed_domains) as egress:
        async def reject_blocked(response):
            if response.headers.get('x-webtask-blocked')=='1':
                raise UnsafeURL((egress.last_denial or {}).get('message','出口拒绝非公网目标'))
        async with httpx.AsyncClient(proxy=egress.url, trust_env=False, timeout=30, follow_redirects=False,
                                     event_hooks={'response':[reject_blocked]}) as client:
            try:
                yield client
            except httpx.ProxyError:
                if egress.last_denial:
                    raise UnsafeURL(egress.last_denial['message'])
                raise
