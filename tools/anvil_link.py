"""Talk to a running ANVIL kernel over QEMU's TCP serial port.

Drives the shell, checks the answers, and writes the transcript to
logs/link.log. Localhost only.

    py tools\\anvil_link.py
"""

from __future__ import annotations

import os
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 4444
PROMPT = b"anvil> "
COMMANDS = ("help", "mem", "heap", "ticks", "exit")


def connect(timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return socket.create_connection((HOST, PORT), timeout=1.0)
        except OSError:
            time.sleep(0.2)
    return None


def read_until(sock: socket.socket, marker: bytes, timeout: float = 6.0):
    buffer = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break              # VM exited and closed the socket
        if not chunk:
            break
        buffer += chunk
        if marker in buffer:
            return buffer, True
    return buffer, False


def main() -> int:
    sock = connect()
    if sock is None:
        print(f"could not connect to ANVIL on {HOST}:{PORT}")
        print("is the VM running? start run_vm_link.bat")
        return 2

    transcript = b""
    boot, ready = read_until(sock, PROMPT, timeout=12.0)
    transcript += boot
    if not ready:
        print("no shell prompt seen. output so far:")
        print(boot.decode("utf-8", "replace"))
        return 1

    for command in COMMANDS:
        sock.sendall((command + "\n").encode())
        if command == "exit":
            transcript += read_until(sock, b"\r\n", timeout=2.0)[0]
            break
        reply, ok = read_until(sock, PROMPT, timeout=5.0)
        transcript += reply
        if not ok:
            print(f"no reply to '{command}'")
            return 1

    sock.close()

    text = transcript.decode("utf-8", "replace")
    print(text)

    logs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, "link.log"), "w", encoding="utf-8") as handle:
        handle.write(text)

    checks = {
        "prompt appeared": "anvil> " in text,
        "help lists commands": "commands: help" in text,
        "mem answered": "usable MB:" in text,
        "heap answered": "arena 0x" in text,
        "ticks answered": "ticks since boot" in text,
    }
    print("checks:")
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
