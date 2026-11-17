"""``scholarforge-license-server keys | serve``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scholarforge-license-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keys", help="generate an Ed25519 signing key pair")
    k.add_argument("--out", required=True, help="path of the private key file (PEM) to create")
    s = sub.add_parser("serve", help="run the HTTP server (configuration from SF_LS_* environment variables)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    args = p.parse_args(argv)
    if args.cmd == "keys":
        from scholarforge.licensing.tokens import generate_keypair

        out = Path(args.out)
        if out.exists():
            print(f"refusing to overwrite {out}", file=sys.stderr)
            return 1
        pem, pub = generate_keypair()
        out.write_text(pem, encoding="ascii")
        if os.name == "posix":
            out.chmod(0o600)
        print(f"private key written to {out}\npublic key (put into scholarforge/licensing/keys.py): {pub}")
        return 0
    import uvicorn

    from scholarforge_license_server.app import create_app
    from scholarforge_license_server.config import ServerConfig

    uvicorn.run(create_app(ServerConfig.from_env()), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
