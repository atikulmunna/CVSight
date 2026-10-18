from __future__ import annotations

import argparse
import os
from pathlib import Path

TOKEN_ENV = "SAM_SEGMENTER_TOKEN"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m runtimes.sam3_segmenter",
        description="Serve SAM 3 click-to-box requests for the CVSight canvas.",
    )
    parser.add_argument("--checkpoint", type=Path, required=True, help="path to sam3.pt")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on; use 0.0.0.0 on a cluster node reached through SSH",
    )
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument(
        "--cache-size",
        type=int,
        default=8,
        help="photos whose encoding is kept, most recently used first",
    )
    args = parser.parse_args(argv)
    # The token comes from the environment so it never shows in the process list.
    token = os.environ.get(TOKEN_ENV, "").strip()
    if len(token) < 32:
        parser.error(f"set {TOKEN_ENV} to a random value of at least 32 characters")

    import uvicorn

    from runtimes.sam3_segmenter.sam3_model import Sam3Segmenter
    from runtimes.sam3_segmenter.service import create_app

    app = create_app(Sam3Segmenter(args.checkpoint), token, args.cache_size)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
