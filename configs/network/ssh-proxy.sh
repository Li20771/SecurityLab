#!/usr/bin/env python3
"""SecurityLab SSH ProxyCommand: tunnel SSH through the WSL host HTTP proxy.

Used from ~/.ssh/config:
    Host github.com
        ProxyCommand python3 /mnt/d/SecurityLab/configs/network/ssh-proxy.sh %h %p

Dynamically resolves the WSL2 NAT gateway (no fixed IP) and issues an HTTP
CONNECT to the Clash/V2Ray mixed port. Pure stdlib, select()-based bidirectional
pipe, works on both Ubuntu and Kali without ncat/corkscrew.
"""
import os
import select
import socket
import subprocess
import sys

PROXY_PORT = 7890
BACKUP_PORTS = (7897, 10809, 10808)
FD_IN = 0
FD_OUT = 1


def default_gateway():
    out = subprocess.run(
        ["ip", "route", "show", "default"], capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            return parts[parts.index("via") + 1]
    return None


def connect_via(gw, port, host, target_port):
    s = socket.create_connection((gw, port), timeout=15)
    req = (
        f"CONNECT {host}:{target_port} HTTP/1.1\r\n"
        f"Host: {host}:{target_port}\r\n"
        "Proxy-Connection: keep-alive\r\n\r\n"
    ).encode()
    s.sendall(req)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            s.close()
            return None, b""
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 200" not in status_line:
        sys.stderr.write(f"ssh-proxy: proxy refused: {status_line}\n")
        s.close()
        return None, b""
    # tunnel established: restore blocking mode with no timeout
    s.settimeout(None)
    return s, rest


def pump(sock, prebuffer):
    if prebuffer:
        os.write(FD_OUT, prebuffer)
    while True:
        try:
            rlist, _, _ = select.select([FD_IN, sock], [], [])
        except (OSError, InterruptedError):
            break
        if FD_IN in rlist:
            try:
                data = os.read(FD_IN, 65536)
            except OSError:
                data = b""
            if not data:
                break
            try:
                sock.sendall(data)
            except OSError:
                break
        if sock in rlist:
            try:
                data = sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            try:
                os.write(FD_OUT, data)
            except OSError:
                break


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: ssh-proxy.sh HOST PORT\n")
        return 2
    host, target_port = sys.argv[1], int(sys.argv[2])
    gw = default_gateway()
    if not gw:
        sys.stderr.write("ssh-proxy: cannot detect WSL default gateway\n")
        return 1

    sock = None
    prebuffer = b""
    last_err = None
    for port in (PROXY_PORT,) + BACKUP_PORTS:
        try:
            sock, prebuffer = connect_via(gw, port, host, target_port)
            if sock is not None:
                break
        except OSError as exc:
            last_err = exc
            continue
    if sock is None:
        sys.stderr.write(
            f"ssh-proxy: CONNECT via {gw} proxy ports failed ({last_err})\n"
        )
        return 1

    try:
        pump(sock, prebuffer)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
