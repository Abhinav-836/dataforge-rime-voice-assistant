#!/bin/bash
# start.sh - Run HTTP server and agent

echo "Starting services on Render with PORT=${PORT:-5500}"

# Start the HTTP server
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py &

# Wait a moment for server to start
sleep 2

# Start the agent with HTTP server disabled via environment
echo "Starting LiveKit agent..."
export LIVEKIT_AGENT_HTTP_PORT="0"
export LIVEKIT_AGENT_PORT="0"
python -m agent.main dev &

# Keep the container alive
wait