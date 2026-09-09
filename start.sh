#!/bin/bash
# start.sh - Use LiveKit CLI with disabled HTTP server

echo "Starting services on Render with PORT=${PORT:-5500}"

# Install livekit-cli if not available
pip install livekit-cli -q

# Start the HTTP server
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py &

# Start the agent using LiveKit CLI with HTTP server disabled
echo "Starting LiveKit agent with HTTP server disabled..."
lk agent dev --watch-path agent --http-port 0 &

# Keep the container alive
wait