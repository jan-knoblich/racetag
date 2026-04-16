#!/usr/bin/env python3
"""
Tiny static server for the frontend folder.
Usage: python3 serve.py [--host 0.0.0.0] [--port 8680]
"""
import argparse
import http.server
import os
import socketserver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8680)
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        print(f"Serving frontend at http://{args.host}:{args.port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
