"""Local server entrypoint used by the Windows launcher."""

import argparse
import logging

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--instance-id', required=True)
    args = parser.parse_args()
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('google_genai').setLevel(logging.WARNING)
    server = None

    def shutdown():
        if server:
            server.should_exit = True

    app = create_app(instance_id=args.instance_id, shutdown=shutdown)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                          access_log=False, log_level='info'))
    server.run()


if __name__ == '__main__':
    main()
