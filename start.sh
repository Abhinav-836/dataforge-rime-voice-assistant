#!/bin/bash
# start.sh - Run both services properly

echo "Starting services on Render with PORT=${PORT:-5500}"

# Start both services in the background
echo "Starting HTTP server on port ${PORT:-5500}..."
python frontend/serve.py &

echo "Starting LiveKit agent..."
python -m agent.main dev &

# Keep the container alive
wait