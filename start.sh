#!/bin/bash

echo "🚀 Audiobook Server - Quick Start"
echo "=================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
cd backend
pip install -r requirements.txt

# Check for .env file
cd ..
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file from example..."
    cp .env.example .env
    echo "✅ .env file created (API key optional for self-hosted servers)"
    echo ""
fi

# Create storage directories if they don't exist
mkdir -p storage/ebooks
mkdir -p storage/audiobooks
mkdir -p storage/lrc

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the server:"
echo "  1. Run: cd backend && python run.py"
echo "  2. Open: http://localhost:8000"
echo "  3. Go to Models tab to add your self-hosted TTS servers"
echo ""
echo "Or run directly:"
echo "  cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
