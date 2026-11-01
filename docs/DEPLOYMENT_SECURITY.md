# Deployment security

## Docker Compose (local / single host)

`docker-compose.yml` is safe by default:

| Service | Published on | Credentials |
|---|---|---|
| postgres | `127.0.0.1:5432` only | `POSTGRES_PASSWORD` is **required** (no default) |
| redis | `127.0.0.1:6379` only | `REDIS_PASSWORD` is **required**; Redis runs with `--requirepass` |
| api | `${AGENTWATCH_BIND_HOST:-127.0.0.1}:8000` | `AGENTWATCH_API_KEY` is required |
| frontend | `${AGENTWATCH_BIND_HOST:-127.0.0.1}:3000` | the server-side proxy attaches the API key |
| jaeger (`tracing` profile) | `127.0.0.1` only | n/a |

Every container runs with `no-new-privileges`. The API, worker and frontend also drop all Linux capabilities and run as non-root users (see the Dockerfiles).

```bash
cp .env.example .env
# fill AGENTWATCH_API_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD, for example with:
python -c "import secrets; print(secrets.token_urlsafe(18))"
python scripts/check_deploy_security.py --env-file .env   # exit 0 = no exposure problems
docker compose up -d
```

Compose refuses to render without the two passwords.

To reach the dashboard from another machine, put a TLS reverse proxy in front and set `AGENTWATCH_BIND_HOST` deliberately. `check_deploy_security.py` then needs `--allow-public api,frontend`. Postgres, Redis and Jaeger are never allowed off loopback.

**Upgrading an existing stack:** containers created from an older `docker-compose.yml` keep their old port bindings: Postgres and Redis on `0.0.0.0` with the old fixed password and no Redis auth. Check with `docker ps`. Recreate them:

```bash
docker compose down
# set the new variables in .env
docker compose up -d
```

## Kubernetes (Helm)

- **Secret required:** the chart does not render without `secrets.existingSecret`, the name of a Secret holding `AGENTWATCH_API_KEY` and, when the bundled services are used, `DATABASE_URL`, `AGENTWATCH_STORE` and `REDIS_URL`. No password lives in `values.yaml` or in the pod spec.

  ```bash
  kubectl create secret generic panoptis-secrets \
    --from-literal=AGENTWATCH_API_KEY=... \
    --from-literal=DATABASE_URL=postgresql+asyncpg://panoptis:...@rel-postgres:5432/panoptis \
    --from-literal=AGENTWATCH_STORE=postgresql+psycopg://panoptis:...@rel-postgres:5432/panoptis \
    --from-literal=REDIS_URL=redis://:...@rel-redis:6379/0
  helm install rel helm --set secrets.existingSecret=panoptis-secrets
  ```

- **Pod security:** `runAsNonRoot` (uid 10001), `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped.
- **Service:** `ClusterIP` by default; exposure goes through your Ingress with TLS.

## Network boundary

Only the API (8000) and dashboard (3000) are meant to be reachable by users, and only through TLS. Postgres and Redis are reachable only by the API and worker, on the Compose network or Kubernetes Services. Never publish them.

## Verification

- `tests/test_deploy_security.py` covers:
  - the old layout is rejected and the hardened one passes;
  - data services are never allowed public, and public app ports need an opt-in;
  - weak passwords are rejected;
  - the repository's Compose file passes the checker and refuses to render without its secrets;
  - the Helm chart requires a Secret, carries no password, and sets the security context.
- CI's compose smoke test runs the checker before starting the stack.
- The deployment smoke run and its results are recorded under TO-009 in `docs/IMPLEMENTATION_EVIDENCE.md`.
