"""The ``themis`` command: one entry point for the gate's tools.

    themis serve      run the local web app and API (127.0.0.1:8742)
    themis hooks      Claude Code hook client (managed-hooks mode), reads the event on stdin
    themis register   job register operator CLI
    themis runtime    local model runtime control
    themis profiles   list deployment profiles (shipped and external)
    themis kmr        known-miss register (v0.3): add, list, verify, reject, promote, export-regression-stub
    themis fpc        reviewed false-positive catalogue (v0.3): propose, accept, list, expire
    themis tripwire   cloud trip-wire results (v0.3): review, list, stats
    themis --version  print the version

Run `themis <command> --help` for a command's options.
"""

from __future__ import annotations

import argparse
import sys


def _profiles(argv: list[str]) -> int:
    from .profiles import KINDS, available_profiles, external_dir

    parser = argparse.ArgumentParser(prog="themis profiles")
    parser.add_argument("--dir", default=None, help="external profiles directory")
    args = parser.parse_args(argv)
    external = external_dir(args.dir)
    print(f"external profiles directory: {external if external else '(none)'}")
    for kind in KINDS:
        names = available_profiles(kind, args.dir)
        print(f"{kind}: {', '.join(names) if names else '(none)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = ("serve", "hooks", "register", "runtime", "profiles", "kmr", "fpc", "tripwire")
    if arguments and arguments[0] in ("--version", "-V"):
        from . import __version__
        print(f"themis-shield {__version__}")
        return 0
    if not arguments or arguments[0] in ("-h", "--help") or arguments[0] not in commands:
        print(__doc__.strip())
        return 0 if arguments and arguments[0] in ("-h", "--help") else 2
    command, rest = arguments[0], arguments[1:]
    if command == "serve":
        from .web.__main__ import main as serve
        result = serve(rest)
        return 0 if result is None else int(result)
    if command == "hooks":
        from .hooks.__main__ import main as hooks
        return int(hooks(rest))
    if command == "register":
        from .register.cli import main as register
        return int(register(rest))
    if command == "runtime":
        from .runtime.__main__ import main as runtime
        return int(runtime(rest))
    if command in ("kmr", "fpc", "tripwire"):
        from .catalogues import cli as catalogues
        return int(getattr(catalogues, command + "_main")(rest))
    return _profiles(rest)


if __name__ == "__main__":
    raise SystemExit(main())
