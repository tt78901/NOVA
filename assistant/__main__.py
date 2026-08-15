"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from .audio import tts
from .audio.mic import list_devices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova", description="A local voice assistant for macOS."
    )
    parser.add_argument("--text", action="store_true", help="type instead of talking")
    parser.add_argument("--once", metavar="QUERY", help="answer one question and exit")
    parser.add_argument(
        "--quiet", action="store_true", help="print the reply, don't speak it"
    )
    parser.add_argument("--config", type=Path, help="path to config.toml")
    parser.add_argument("--no-ui", action="store_true", help="don't serve the HUD")
    parser.add_argument(
        "--ui-only", action="store_true", help="serve the HUD and nothing else"
    )
    parser.add_argument(
        "--devices", action="store_true", help="list audio devices and exit"
    )
    parser.add_argument(
        "--voices", action="store_true", help="list speech voices and exit"
    )
    return parser


def _ui_only(cfg) -> int:
    import time
    import webbrowser

    from .ui.server import EventBus, UIServer

    bus = EventBus()
    server = UIServer(bus, cfg.ui.port)
    url = server.start()
    print(f"HUD at {url}  (Ctrl-C to stop)")
    bus.publish(
        "info", name=cfg.name, model=cfg.llm.model, wake=cfg.wake.phrase
    )
    if cfg.ui.open_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.devices:
        print(list_devices())
        return 0
    if args.voices:
        print("\n".join(tts.available_voices()))
        print(f"\nAuto-selected: {tts.pick_voice()}")
        return 0

    try:
        cfg = config_mod.load(args.config)
    except ValueError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.quiet:
        cfg.tts.enabled = False
    if args.no_ui:
        cfg.ui.enabled = False

    if args.ui_only:
        # Preview the HUD on its own, with no models loaded. Handy for design
        # work: it animates from idle without a microphone attached.
        return _ui_only(cfg)

    from .runtime import Assistant  # deferred: pulls in the heavy audio stack

    assistant = Assistant(cfg)
    try:
        if args.once:
            assistant.warm(voice=False)
            assistant.respond(args.once)
        elif args.text:
            assistant.run_text()
        else:
            assistant.run_voice()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        assistant.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
