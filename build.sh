#!/usr/bin/env bash
# Build step used locally and on Render: deps -> synthetic data -> C engine.
set -e
pip install -r requirements.txt
python -m data.generate_data
gcc -O2 -shared -fPIC -o engine/libsentinel.so engine/graph_engine.c
echo "Build complete."
