#!/bin/bash
# start.sh - Run both services properly

echo "Starting services on Render with PORT=${PORT:-5500}"

# IMPORTANT: Disable agent's internal HTTP server
export LIVEKIT_AGENT_HTTP_PORT=""
export LIVEKIT_AGENT_PORT="0"

# Start the HTTP server
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py &

# Start the LiveKit agent with the environment variables set
echo "Starting LiveKit agent..."
env LIVEKIT_AGENT_HTTP_PORT="" LIVEKIT_AGENT_PORT="0" python -m agent.main dev &

# Keep the container alive
wait