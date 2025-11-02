#!/bin/bash
set -e
echo "Installing Go..."
apt-get update && apt-get install -y golang-go git

echo "Building GSwarm..."
go install github.com/Deep-Commit/gswarm/cmd/gswarm@latest
echo "✅ GSwarm built at ~/go/bin/gswarm"
