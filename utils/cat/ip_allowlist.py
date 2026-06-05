"""IP allowlist for the optional standalone CAT bridge process.

Mirrors the behaviour of the cat-ftx1 ``ip-allowlist.mjs`` so the two
projects interpret the same ``ALLOWED_IPS`` environment variable
identically. Used by :mod:`cat_bridge` to gate both the HTTP API and the
rigctld TCP relay.

Format (comma-separated; each entry a single address or a CIDR)::

    ALLOWED_IPS="127.0.0.1/8,::1,192.168.1.0/24"

Unset / empty -> defaults to loopback only (secure-by-default).

IPv4-mapped IPv6 addresses (``::ffff:192.168.1.5``) are normalised to
their IPv4 form before the lookup.

This module is part of the *optional* bridge feature. Nothing in the
main intercept app imports it, so the base application keeps working with
no extra configuration.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Iterable

logger = logging.getLogger('intercept.cat.allowlist')

DEFAULT_ALLOWED_IPS = '127.0.0.1/8,::1'


class IpAllowlist:
    """Parsed ``ALLOWED_IPS`` spec with a fast membership check."""

    def __init__(self, spec: str | None = None) -> None:
        raw = spec if spec is not None else os.environ.get('ALLOWED_IPS')
        trimmed = (raw or '').strip()
        self.source = 'env' if trimmed else 'default'
        self.spec = trimmed or DEFAULT_ALLOWED_IPS
        self._networks: list[ipaddress._BaseNetwork] = []
        self.rejected: list[str] = []
        self._build(self.spec)

    def _build(self, spec: str) -> None:
        for entry in (e.strip() for e in spec.split(',')):
            if not entry:
                continue
            try:
                if '/' in entry:
                    net = ipaddress.ip_network(entry, strict=False)
                else:
                    # A bare address becomes a host network (/32 or /128).
                    net = ipaddress.ip_network(entry, strict=False)
                self._networks.append(net)
            except ValueError:
                self.rejected.append(entry)
        if self.rejected:
            logger.warning(
                'ALLOWED_IPS rejected invalid entries: %s',
                ', '.join(self.rejected),
            )

    @staticmethod
    def _normalise(remote: str | None) -> str | None:
        if not remote:
            return None
        ip = remote.strip()
        # Strip IPv4-mapped IPv6 prefix so "::ffff:192.168.1.5" matches
        # an IPv4 rule for 192.168.1.5.
        if ip.startswith('::ffff:') and '.' in ip:
            ip = ip[len('::ffff:'):]
        return ip

    def is_allowed(self, remote_address: str | None) -> bool:
        """Return ``True`` when ``remote_address`` is on the allowlist.

        Safe to call with whatever a socket's ``remoteAddress`` returns:
        ``None``, IPv4, IPv6, or IPv4-mapped IPv6.
        """
        ip_str = self._normalise(remote_address)
        if not ip_str:
            return False
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for net in self._networks:
            # ip_network and ip_address must share a version to compare.
            if ip.version != net.version:
                continue
            if ip in net:
                return True
        return False


_cached: IpAllowlist | None = None


def get_allowlist() -> IpAllowlist:
    """Return a process-wide cached :class:`IpAllowlist` from ``ALLOWED_IPS``."""
    global _cached
    if _cached is None:
        _cached = IpAllowlist()
        logger.info(
            'CAT bridge allowlist active (from %s): "%s"',
            _cached.source, _cached.spec,
        )
    return _cached


def is_allowed_remote_address(remote_address: str | None) -> bool:
    """Module-level convenience wrapper over the cached allowlist."""
    return get_allowlist().is_allowed(remote_address)


def get_allowed_ips_spec() -> str:
    """Return the effective allowlist spec string (for logging / status)."""
    return get_allowlist().spec
