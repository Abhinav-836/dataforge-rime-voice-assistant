#!/bin/bash
# start.sh - Using LiveKit CLI for better control

echo "Starting services on Render with PORT=${PORT:-5500}"

# Install LiveKit CLI if not present
# pip install livekit-cli

# Start the HTTP server in the background
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py &

# Start the agent using LiveKit CLI (this doesn't start an HTTP server)
echo "Starting LiveKit agent..."
lk agent dev --watch-path agent

# Wait for all processes
wait