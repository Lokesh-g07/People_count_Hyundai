"""
validation.py — Source validation, SSRF protection, and upload checks
======================================================================
"""

import ipaddress
import logging
import socket
from pathlib import Path
from urllib.parse import urlparse

from config import UPLOAD_DIR, RTSP_PATTERN, RTSP_ALLOWLIST

logger = logging.getLogger(__name__)


def validate_source(source: str) -> str:
    """
    Validate and normalise a video source string.
    - Camera indices (digits) → allowed with a warning
    - Local file paths → must resolve inside UPLOAD_DIR
    - RTSP URLs → must match the expected pattern, resolve to safe IP
    - Everything else → rejected
    """
    s = source.strip()
    if not s:
        raise ValueError("Empty source")

    # Camera index (e.g. "0", "1")
    if s.isdigit():
        logger.info(f"Source is a server-side camera device index: {s}")
        return s

    # RTSP stream
    if s.lower().startswith("rtsp"):
        if not RTSP_PATTERN.match(s):
            raise ValueError("Invalid source format")

        parsed = urlparse(s)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid RTSP URL format")

        try:
            addrinfo = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
            resolved_ips = {info[4][0] for info in addrinfo}
        except socket.gaierror:
            raise ValueError("Could not resolve source host")

        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise ValueError("Invalid resolved IP")

            # Allowlist check
            is_allowlisted = any(ip in net for net in RTSP_ALLOWLIST)

            if not is_allowlisted:
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_unspecified
                    or ip.is_multicast
                    or ip.is_reserved
                ):
                    raise ValueError(
                        "Source IP address is not permitted by policy"
                    )

        return s

    # Local file — must be within UPLOAD_DIR
    try:
        resolved = Path(s).resolve()
        upload_resolved = UPLOAD_DIR.resolve()
        if not resolved.is_relative_to(upload_resolved):
            raise ValueError("Source path must be within upload directory")
        if not resolved.exists():
            raise ValueError("File not found")
        return str(resolved)
    except (OSError, ValueError):
        raise ValueError("Invalid local path")


def check_magic_bytes(header: bytes) -> bool:
    """Validate video file magic bytes."""
    if header.startswith(b"\x00\x00\x00") and b"ftyp" in header[:16]:
        return True  # MP4/MOV
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ":
        return True
    if header.startswith(b"\x1A\x45\xDF\xA3"):
        return True  # MKV
    if header.startswith(b"\x47"):
        return True  # TS sync byte
    return False
