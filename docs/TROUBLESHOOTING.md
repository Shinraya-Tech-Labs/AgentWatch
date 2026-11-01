# Troubleshooting Guide

This guide covers common installation, configuration, and runtime issues that users may encounter while setting up AgentWatch.

---

## Environment Setup Issues

### Missing `.env` File

**Problem**

Application fails to start or reports missing configuration values.

**Solution**

Copy the example environment file:

```bash
cp .env.example .env
```

Update the required values before running the application.

---

### Invalid Environment Variables

**Problem**

Services fail to connect or startup errors occur.

**Solution**

Verify the values in `.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/panoptis
REDIS_URL=redis://localhost:6379/0
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Ensure there are no extra spaces or invalid characters.

---

## PostgreSQL Issues

### Connection Refused

**Error**

```text
could not connect to server
connection refused
```

**Cause**

PostgreSQL is not running.

**Solution**

Start PostgreSQL and verify the service is active.

Check connection details:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=panoptis
DB_USER=postgres
```

---

### Incorrect DATABASE_URL

**Problem**

Application cannot connect to the database.

**Solution**

Verify the connection string:

```env
DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/panoptis
```

Ensure:

- Username is correct
- Password is correct
- Database exists
- PostgreSQL is running

---

## Redis Issues

### Redis Not Running

**Error**

```text
Error connecting to Redis
Connection refused
```

**Solution**

Start Redis:

```bash
redis-server
```

Verify Redis is responding:

```bash
redis-cli ping
```

Expected output:

```text
PONG
```

---

### Redis Timeout Errors

**Error**

```text
RedisTimeoutError
```

**Possible Causes**

- Redis service overloaded
- Incorrect Redis URL
- Network connectivity issues

**Solution**

Verify Redis URL:

```env
REDIS_URL=redis://localhost:6379/0
```

Restart Redis and try again.

Check Redis logs for additional details.

---

### Incorrect Redis Configuration

**Problem**

Background services fail to communicate with Redis.

**Solution**

Verify:

```env
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

---

## Celery Issues

### Celery Worker Not Starting

**Cause**

Broker or backend configuration is invalid.

**Solution**

Verify:

```env
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/1
```

Ensure Redis is running before starting Celery.

---

### Task Execution Failures

**Problem**

Tasks remain pending or never complete.

**Solution**

- Verify Redis is reachable
- Restart Celery workers
- Check worker logs for exceptions

---

## Lock Exceptions

### Resource Lock Errors

**Problem**

Operations fail because a lock cannot be acquired.

**Possible Causes**

- Another process holds the lock
- Previous execution did not release the lock properly

**Solution**

- Wait for the active operation to complete
- Restart affected services if necessary
- Review logs to identify the process holding the lock

---

## Docker Issues

### Docker Compose Fails to Start

**Problem**

Containers exit immediately or fail during startup.

**Solution**

Check logs:

```bash
docker compose logs
```

Rebuild services:

```bash
docker compose down
docker compose up --build -d
```

---

### Port Already in Use

**Error**

```text
Bind for 0.0.0.0 failed: port is already allocated
```

**Solution**

Stop the application using the port or update the port mapping.

Common ports:

- 3000 (Frontend)
- 8000 (API)
- 5432 (PostgreSQL)
- 6379 (Redis)

---

## Python Dependency Issues

### ModuleNotFoundError

**Error**

```text
ModuleNotFoundError
```

**Solution**

Install dependencies:

```bash
pip install -e ".[dev]"
```

---

### Incorrect Python Version

**Problem**

Dependency installation or runtime errors occur.

**Solution**

Use Python 3.12 or newer as required by the project.

Check version:

```bash
python --version
```

---

## Frontend Issues

### npm Install Errors

**Solution**

Clear dependencies and reinstall:

```bash
cd frontend
npm install
```

---

### Dashboard Not Loading

**Problem**

Frontend starts but cannot communicate with the backend.

**Solution**

Verify:

```env
NEXT_PUBLIC_API_HOST=localhost:8000
```

Ensure backend services are running:

- API: http://localhost:8000
- Dashboard: http://localhost:3000

---

## API Authentication Issues

### Missing AGENTWATCH_API_KEY

**Problem**

Requests fail with authentication errors.

**Solution**

Configure:

```env
AGENTWATCH_API_KEY=your_api_key_here
```

Restart the application after updating the value.

---

## Getting Additional Help

If the issue persists:

1. Check existing GitHub Issues.
2. Review application logs.
3. Include the following when reporting a problem:
   - Operating System
   - Python Version
   - Docker Version
   - Error Message
   - Relevant Logs

Providing complete information helps contributors diagnose issues more quickly.