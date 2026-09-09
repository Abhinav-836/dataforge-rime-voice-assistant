#!/bin/bash
# start.sh - Simple process launcher for Render

echo "Starting services on Render with PORT=${PORT:-5500}"

# Start the LiveKit agent in dev mode (background)
echo "Starting LiveKit agent..."
python -m agent.main dev &

# Start the HTTP server (foreground - keeps container alive)
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py