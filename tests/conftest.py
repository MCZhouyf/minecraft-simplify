"""Shared fixtures for IAP-Agent tests.

Integration tests need a LIVE Minecraft 1.19 world opened to LAN. Gate them
behind the IAP_MC_PORT environment variable so `pytest -m "not integration"`
always works offline.

Run integration suite:
    1) start Minecraft, open the world to LAN (cheats ON), note the port
    2) export IAP_MC_PORT=<port>     (Windows: set IAP_MC_PORT=<port>)
    3) pytest tests/integration -m integration -x
"""
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs a live Minecraft LAN world (set IAP_MC_PORT)"
    )


def detect_lan_port():
    env_port = os.environ.get("IAP_MC_PORT") or os.environ.get("ADAM_MC_PORT")
    if env_port:
        os.environ["IAP_MC_PORT"] = env_port
        return int(env_port)

    log_path = Path("/root/.minecraft/logs/latest.log")
    if not log_path.exists():
        return None

    patterns = (
        re.compile(r"Started serving on (\d+)"),
        re.compile(r"Local game hosted on port (\d+)"),
    )

    try:
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None

    for line in reversed(lines):
        for pattern in patterns:
            match = pattern.search(line)
            if not match:
                continue
            port = int(match.group(1))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
            except OSError:
                continue
            os.environ["IAP_MC_PORT"] = str(port)
            return port
    return None


def detect_mineflayer_port():
    env_port = os.environ.get("IAP_MF_PORT")
    if env_port:
        return int(env_port)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    os.environ["IAP_MF_PORT"] = str(port)
    return port


def pytest_collection_modifyitems(config, items):
    if detect_lan_port() is not None:
        return
    skip = pytest.mark.skip(
        reason="IAP_MC_PORT not set and no active LAN port detected; integration tests skipped"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ----------------------------------------------------------------- live env fixture
@pytest.fixture(scope="session")
def env():
    """Session-scoped VoyagerEnv connected to the LAN world."""
    from env.bridge import VoyagerEnv

    port = detect_lan_port()
    if port is None:
        raise RuntimeError(
            "No active Minecraft LAN port detected. Open the world to LAN or set IAP_MC_PORT."
        )
    server_port = detect_mineflayer_port()
    e = VoyagerEnv(mc_port=port, server_port=server_port, request_timeout=120)
    # Establish the mineflayer session ONCE before any fixture/test calls
    # env.step() (fixes: "Environment has not been reset yet").
    e.reset(options={"mode": "hard", "inventory": {}})
    yield e
    try:
        e.close()
    except Exception:
        pass


@pytest.fixture(scope="session")
def world_save():
    from mc_drift.datapack_gen import load_config, world_save_path
    return world_save_path(load_config())


# ----------------------------------------------------------------- helpers
def reset_with(env, inventory: dict, position=None):
    """Hard reset: clears the bot and /give's the requested inventory."""
    options = {"mode": "hard", "inventory": inventory}
    if position is not None:
        options["position"] = {"x": position[0], "y": position[1], "z": position[2]}
    return env.reset(options=options)


def run_chat(env, *commands, wait_ticks=20):
    """Run raw chat/server commands through /step (TESTS ONLY — never in the agent)."""
    code = "".join(
        f'bot.chat({json.dumps(c)});\nawait bot.waitForTicks({wait_ticks});\n'
        for c in commands
    )
    return env.step(code)


def run_action(env, action_name: str):
    """Execute one ActionLib skill exactly the way ADAM does."""
    from Adam.skill_loader import skill_loader
    return env.step(skill_loader(action_name))


def extract_inventory(obs) -> dict:
    """Defensively pull the LAST inventory dict out of an observation payload."""
    found = [{}]

    def walk(node):
        if isinstance(node, dict):
            inv = node.get("inventory")
            if isinstance(inv, dict):
                found.append(inv)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obs)
    return found[-1]


def count_of(obs, item_name: str) -> int:
    inv = extract_inventory(obs)
    total = 0
    for k, v in inv.items():
        if k == item_name and isinstance(v, (int, float)):
            total += int(v)
    return total
