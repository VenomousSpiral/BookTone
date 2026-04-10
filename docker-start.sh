#!/bin/bash

# Audiobook Server Docker Quick Start Script

echo "🎵 Audiobook Server - Docker Setup"
echo "=================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose:"
    echo "   https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Navigate to docker directory
cd "$(dirname "$0")/docker" || exit 1

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from example..."
    cp .env.example .env
    echo "✅ Created docker/.env file"
    echo "ℹ️  You can edit docker/.env to change PORT and other settings"
else
    echo "✅ docker/.env file exists"
fi

# Create storage directories if they don't exist
echo "📂 Ensuring storage directories exist..."
mkdir -p ../storage/ebooks
mkdir -p ../storage/audiobooks
mkdir -p ../storage/lrc
echo "✅ Storage directories ready"

# Initialize database if it doesn't exist
if [ ! -f ../storage/audiobooks_db.json ]; then
    echo "📝 Creating initial database..."
    echo "{}" > ../storage/audiobooks_db.json
    echo "✅ Database initialized"
else
    echo "✅ Database already exists"
fi

echo ""
echo "⚠️  IMPORTANT: Make sure your TTS server(s) are running separately!"
echo "   Configure TTS server URLs in models.json"
echo "   The container can access localhost, network IPs, and public URLs."
echo ""
echo "🚀 Starting audiobook server with Docker Compose..."
echo ""

# Start services
docker-compose up -d

# Wait for services to be ready
echo ""
echo "⏳ Waiting for server to start..."
sleep 3

# Check if services are running
if docker-compose ps | grep -q "Up"; then
    # Read PORT from .env file if it exists, default to 8000
    PORT=8000
    if [ -f docker/.env ]; then
        PORT_LINE=$(grep "^PORT=" docker/.env | tail -1)
        if [ -n "$PORT_LINE" ]; then
            PORT=$(echo "$PORT_LINE" | cut -d '=' -f 2)
        fi
    fi
    
    echo ""
    echo "✅ Audiobook server is running!"
    echo ""
    echo "📍 Access the application at:"
    echo "   🌐 Web Interface: http://localhost:$PORT"
    echo ""
    echo "⚠️  Remember: TTS server(s) must be running separately"
    echo "   Configure URLs in models.json (supports localhost, network IPs, and cloud URLs)"
    echo ""
    echo "📊 View logs:"
    echo "   docker-compose -f docker/docker-compose.yml logs -f"
    echo ""
    echo "🛑 Stop server:"
    echo "   docker-compose -f docker/docker-compose.yml down"
    echo ""
else
    echo ""
    echo "⚠️  Server may not have started correctly. Check logs:"
    echo "   docker-compose logs"
    echo ""
fi
