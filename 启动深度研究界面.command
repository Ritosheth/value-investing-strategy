#!/bin/bash
cd "$(dirname "$0")"
exec /usr/bin/python3 deep-stock-research/ui_server.py --open-browser
