from __future__ import annotations

import argparse
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .gateway import create_gateway


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
WRITING_ROOT = WORKSPACE_ROOT / "09-HaloCue-1.0-Writing"
PRODUCTION_ROOT = WORKSPACE_ROOT / "08-HaloCue-1.0"
for source_root in (WRITING_ROOT / "src", PRODUCTION_ROOT / "src"):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from halocue_production.app import create_server as create_production_server  # noqa: E402
from halocue_production.config import Settings  # noqa: E402
from halocue_production.service import ProductionService  # noqa: E402
from halocue_writing.app import make_handler  # noqa: E402
from halocue_writing.service import WritingService  # noqa: E402


class IntegratedRuntime:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        writing_data_dir: Path,
        production_data_dir: Path,
    ) -> None:
        settings = Settings.from_env(host="127.0.0.1", port=0, data_dir=production_data_dir)
        self.production_service = ProductionService(settings)
        self.production_server = create_production_server(self.production_service, "127.0.0.1", 0)
        production_address = ("127.0.0.1", self.production_server.server_port)

        def publish_formal_artifact(
            uri: str,
            content: bytes,
            *,
            kind: str,
            media_type: str,
            metadata: dict[str, object],
        ):
            """Bridge immutable writing bytes into the shared local ArtifactStore."""
            return self.production_service.artifacts.commit_bytes(
                uri,
                content,
                kind=kind,
                media_type=media_type,
                metadata=metadata,
            )

        self.writing_service = WritingService(
            writing_data_dir,
            f"http://127.0.0.1:{self.production_server.server_port}",
            public_production_url="/production",
            formal_artifact_publisher=publish_formal_artifact,
        )
        writing_handler = make_handler(self.writing_service, WRITING_ROOT / "web")
        self.writing_server = ThreadingHTTPServer(("127.0.0.1", 0), writing_handler)
        writing_address = ("127.0.0.1", self.writing_server.server_port)

        self.gateway = create_gateway(
            host,
            port,
            writing_address=writing_address,
            production_address=production_address,
            static_dir=PROJECT_ROOT / "static",
        )
        self._threads = [
            threading.Thread(
                target=self._serve_upstream,
                args=(self.production_server,),
                name="halocue-production",
                daemon=True,
            ),
            threading.Thread(
                target=self._serve_upstream,
                args=(self.writing_server,),
                name="halocue-writing",
                daemon=True,
            ),
        ]
        self._upstreams_started = False
        self._gateway_thread: threading.Thread | None = None
        self._closed = False

    @property
    def port(self) -> int:
        return self.gateway.server_port

    def start_upstreams(self) -> None:
        if self._closed:
            raise RuntimeError("integrated runtime is closed")
        if self._upstreams_started:
            return
        for thread in self._threads:
            thread.start()
        self._upstreams_started = True

    @staticmethod
    def _serve_upstream(server: ThreadingHTTPServer) -> None:
        server.halocue_serving = True
        try:
            server.serve_forever()
        finally:
            server.halocue_serving = False

    def start(self) -> None:
        """Start both domain services and the single public gateway."""
        self.start_upstreams()
        if self._gateway_thread is None or not self._gateway_thread.is_alive():
            self._gateway_thread = threading.Thread(
                target=self.gateway.serve_forever,
                name="halocue-gateway",
                daemon=True,
            )
            self._gateway_thread.start()

    def serve_forever(self) -> None:
        """Run the composition root in the foreground for CLI entry points."""
        self.start_upstreams()
        try:
            self.gateway.serve_forever()
        finally:
            self.close(stop_gateway=False)

    @staticmethod
    def _stop_server(server: ThreadingHTTPServer, *, serving: bool) -> None:
        if serving:
            server.shutdown()
        server.server_close()

    def close(self, *, stop_gateway: bool = True) -> None:
        if self._closed:
            return
        if stop_gateway:
            self._stop_server(self.gateway, serving=self.gateway.halocue_serving)
        else:
            self._stop_server(self.gateway, serving=False)
        self._stop_server(
            self.writing_server,
            serving=bool(getattr(self.writing_server, "halocue_serving", False)),
        )
        self._stop_server(
            self.production_server,
            serving=bool(getattr(self.production_server, "halocue_serving", False)),
        )
        self.production_service.jobs.close()
        for thread in self._threads:
            thread.join(timeout=3)
        if self._gateway_thread and self._gateway_thread is not threading.current_thread():
            self._gateway_thread.join(timeout=3)
        self._closed = True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="HaloCue 1.0 integrated writing and AA production runtime")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8910)
    parser.add_argument("--writing-data-dir")
    parser.add_argument("--production-data-dir")
    args = parser.parse_args(argv)
    writing_data = Path(args.writing_data_dir or os.getenv("HALOCUE_WRITING_DATA_DIR") or WRITING_ROOT / "data").resolve()
    production_data = Path(args.production_data_dir or os.getenv("HALOCUE_DATA_DIR") or PRODUCTION_ROOT / "data").resolve()
    runtime = IntegratedRuntime(
        host=args.host,
        port=args.port,
        writing_data_dir=writing_data,
        production_data_dir=production_data,
    )
    print(f"HaloCue 1.0: http://{args.host}:{runtime.port}/", flush=True)
    print(f"AA production: http://{args.host}:{runtime.port}/production/", flush=True)
    try:
        runtime.serve_forever()
    except KeyboardInterrupt:
        runtime.close()


if __name__ == "__main__":
    main()
