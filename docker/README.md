# Docker Deployment Guide

This directory contains Docker Compose configuration for running the Audiobook Server in a container.

**Note:** The TTS server is managed separately and is not included in this Docker setup. Configure your TTS server(s) in `models.json`.

## 📋 Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- One or more TTS servers running (on host, network, or cloud)
- 2GB+ RAM recommended

## 🚀 Quick Start

### 1. Start Your TTS Server(s)

Make sure your TTS server(s) are running and accessible. Examples:
```bash
# Local TTS server on host: http://localhost:8880/v1
# Network TTS server: http://192.168.1.100:5000/v1
# Cloud TTS server: https://api.your-tts-service.com/v1
```

### 2. Configure TTS Server(s) in models.json

Edit `../models.json` to add all your TTS servers:

```json
{
  "local-tts": {
    "name": "local-tts",
    "voices": ["voice1", "voice2"],
    "base_url": "http://localhost:8880/v1",
    "api_key": null
  },
  "network-tts": {
    "name": "network-tts",
    "voices": ["alloy", "echo"],
    "base_url": "http://192.168.1.100:5000/v1",
    "api_key": null
  },
  "cloud-tts": {
    "name": "cloud-tts",
    "voices": ["coral", "sage"],
    "base_url": "https://api.your-service.com/v1",
    "api_key": "your-api-key-here"
  }
}
```

**Important:** The container uses `network_mode: host`, so it can access:
- `localhost` services (TTS on host machine)
- Local network IPs (TTS on other machines)
- Public URLs (cloud TTS services)

### 3. Configure Environment (Optional)

```bash
cd docker
cp .env.example .env
# Edit .env if needed (defaults work for most cases)
```

**Important:** The `docker-compose.yml` expects the `.env` file to be in the `docker/` directory.

### 4. Start Audiobook Server

**Option A: Using the convenience script (from project root)**
```bash
./docker-start.sh
```

**Option B: Using docker-compose directly (from docker directory)**
```bash
cd docker
docker-compose up -d
```

**Option C: Using docker-compose from project root**
```bash
docker-compose -f docker/docker-compose.yml up -d
```

This will:
- Build the audiobook server image
- Create persistent volumes for storage
- Start the audiobook server container with host network access

### 5. Access Application

- **Web Interface:** http://localhost:8000

Your TTS server(s) will be accessed as configured in `models.json`

### 6. Check Status

```bash
# View logs
docker-compose logs -f

# Check container status
docker-compose ps
```

## 🔧 Configuration Options

### Path Compatibility (LRC Files)

The entrypoint script automatically handles path compatibility between host and Docker environments. If you have audiobooks generated on the host system (non-Docker) and want to use them in Docker, the container will automatically create a symlink to resolve the path differences. This ensures LRC (lyrics) files display correctly regardless of where the audiobook was generated.

### Multiple TTS Servers

You can configure as many TTS servers as you want in `models.json`. Each will appear as a separate model option when generating audiobooks:

```json
{
  "fast-tts": {
    "name": "fast-tts",
    "voices": ["voice1"],
    "base_url": "http://localhost:8880/v1",
    "api_key": null
  },
  "quality-tts": {
    "name": "quality-tts",
    "voices": ["voice2", "voice3"],
    "base_url": "http://192.168.1.50:9000/v1",
    "api_key": null
  },
  "cloud-tts": {
    "name": "cloud-tts",
    "voices": ["alloy", "echo"],
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-your-key-here"
  }
}
```

### Network Access

The container uses `network_mode: host`, which means:
- ✅ Can access `localhost` (services on host machine)
- ✅ Can access local network IPs (192.168.x.x, 10.x.x.x)
- ✅ Can access public internet URLs
- ✅ No network isolation (same as running directly on host)

### Changing Configuration

After editing `models.json`, restart the container:
```bash
docker-compose restart
```
            capabilities: [gpu]
```

## 📂 Data Persistence

### Volumes

- `../storage` → Ebooks, audiobooks, LRC files, database (mounted as bind mount)

### Backup

```bash
# Backup storage directory
tar -czf audiobook-backup-$(date +%Y%m%d).tar.gz ../storage/

# Backup models configuration
cp ../models.json ../models.json.backup
```

### Restore

```bash
# Restore storage
tar -xzf audiobook-backup-YYYYMMDD.tar.gz -C ../

# Restore models
cp ../models.json.backup ../models.json

# Restart container
docker-compose restart
```

## 🛠️ Management Commands

### Start/Stop Services

```bash
# Start audiobook server
docker-compose up -d

# Stop audiobook server
docker-compose down

# Restart audiobook server
docker-compose restart
```

### View Logs

```bash
# View logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100
```

### Rebuild After Code Changes

```bash
# Rebuild and restart
docker-compose up -d --build

# Force rebuild without cache
docker-compose build --no-cache
docker-compose up -d
```

### Execute Commands in Container

```bash
# Open shell in backend container
docker-compose exec audiobook-server /bin/bash

# Run Python command
docker-compose exec audiobook-server python -c "print('Hello')"

# Check Python packages
docker-compose exec audiobook-server pip list
```

## 🔍 Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs audiobook-server

# Check container status
docker ps -a | grep audiobook

# Recreate container
docker-compose up -d --force-recreate audiobook-server
```

### TTS Server Connection Issues

```bash
# Test TTS server from host
curl -X POST http://localhost:8880/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","voice":"am_onyx","input":"Test"}' \
  --output test.mp3

# Check if TTS server is accessible
curl http://localhost:8880/v1/models

# Verify TTS server is running
# Check your TTS server logs/status separately
```

### Permission Issues

```bash
# Fix storage permissions
sudo chown -R $USER:$USER ../storage

# Recreate with correct permissions
docker-compose down
docker-compose up -d
```

### Out of Disk Space

```bash
# Check disk usage
docker system df

# Clean up unused images/containers
docker system prune -a

# Remove unused volumes
docker volume prune
```

### Port Already in Use

```bash
# Check what's using port 8000
sudo lsof -i :8000

# Change port in docker-compose.yml
ports:
  - "8001:8000"  # Host:Container
```

## 🌐 Network Access

### Access from Other Devices

1. Find your server's IP:
```bash
ip addr show | grep inet
```

2. Access from browser on same network:
```
http://YOUR_SERVER_IP:8000
```

3. (Optional) Set up reverse proxy for HTTPS:
```yaml
# Add to docker-compose.yml
nginx:
  image: nginx:alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./nginx.conf:/etc/nginx/nginx.conf
    - ./ssl:/etc/nginx/ssl
  depends_on:
    - audiobook-server
```

## 📊 Monitoring

### Resource Usage

```bash
# Monitor resource usage
docker stats

# Check specific container
docker stats audiobook-server

# Export metrics
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

### Health Checks

```bash
# Check health status
docker-compose ps

# Inspect health
docker inspect audiobook-server | jq '.[0].State.Health'
```

## 🔒 Security Considerations

### Network Isolation

The default configuration uses a bridge network. For production:

1. Use a reverse proxy with authentication
2. Enable HTTPS
3. Restrict port access with firewall
4. Use environment variables for sensitive data

### Example with Traefik

```yaml
audiobook-server:
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.audiobook.rule=Host(`audiobooks.yourdomain.com`)"
    - "traefik.http.routers.audiobook.tls=true"
    - "traefik.http.routers.audiobook.tls.certresolver=letsencrypt"
```

## 🔄 Updates

### Update Application

```bash
# Pull latest code
cd /home/eli/AI-projects/auido_book_server
git pull  # or update files manually

# Rebuild and restart
cd docker
docker-compose up -d --build
```

### Update TTS Server

```bash
# Pull latest image
docker-compose pull tts-server

# Restart with new image
docker-compose up -d tts-server
```

## 📝 Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port (change in .env to use different port) |
| `DEBUG` | `True` | Enable debug mode |
| `OPENAI_API_KEY` | _(optional)_ | OpenAI API key (for direct OpenAI usage) |
| `OPENAI_BASE_URL` | _(optional)_ | Default OpenAI base URL |
| `TZ` | `America/New_York` | Container timezone |

**Note:** TTS servers are configured in `models.json`, not environment variables. Each model can have its own `base_url` and `api_key`.

## 🎯 Production Checklist

- [ ] Configure reverse proxy (nginx/Traefik)
- [ ] Enable HTTPS with Let's Encrypt
- [ ] Set up authentication
- [ ] Configure automated backups
- [ ] Set resource limits in docker-compose.yml
- [ ] Enable logging to external service
- [ ] Configure monitoring/alerting
- [ ] Test disaster recovery procedures
- [ ] Document custom configuration
- [ ] Set up firewall rules

## 📞 Support

For issues:
1. Check logs: `docker-compose logs -f`
2. Verify configuration in `.env` and `models.json`
3. Test TTS server connectivity
4. Review main documentation in `../README_COMPLETE.md`
