#!/usr/bin/env bash
# AgentWatch — Railway full deployment script
# Reads RAILWAY_TOKEN from .env in the project root.
# Usage: bash scripts/deploy_railway.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load env file
if [[ -f "$ROOT/.env" ]]; then
  set -a; source "$ROOT/.env"; set +a
fi

: "${RAILWAY_TOKEN:?RAILWAY_TOKEN must be set in .env}"

API="https://backboard.railway.app/graphql/v2"

gql() {
  local query="$1"
  local vars="${2:-{}}"
  curl -sf -X POST "$API" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $RAILWAY_TOKEN" \
    -d "{\"query\":$(echo "$query" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"variables\":$vars}"
}

step() { echo -e "\n\033[1;34m▶ $1\033[0m"; }
ok()   { echo -e "  \033[92m✓ $1\033[0m"; }
info() { echo -e "  \033[2m$1\033[0m"; }

# ── 1. Verify auth ────────────────────────────────────────────
step "Verifying Railway auth"
ME=$(gql '{ me { id name email workspaces { id name } } }')
USER_NAME=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data']['me']['name'])")
WORKSPACE_ID=$(echo "$ME" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['data']['me']['workspaces'][0]['id'])")
ok "Logged in as $USER_NAME  workspace=$WORKSPACE_ID"

# ── 2. Get or create project ──────────────────────────────────
step "Setting up Railway project"
PROJECTS=$(gql '{ me { projects { edges { node { id name } } } } }')
PROJECT_ID=$(echo "$PROJECTS" | python3 -c "
import json,sys
edges = json.load(sys.stdin)['data']['me']['projects']['edges']
hit = [e['node']['id'] for e in edges if e['node']['name']=='panoptis']
print(hit[0] if hit else '')
")

if [[ -z "$PROJECT_ID" ]]; then
  PROJ=$(gql "mutation(\$w: String!) { projectCreate(input: { name: \"panoptis\", isPublic: false, workspaceId: \$w }) { id name } }" \
              "{\"w\":\"$WORKSPACE_ID\"}")
  PROJECT_ID=$(echo "$PROJ" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['projectCreate']['id'])")
  ok "Created project  id=$PROJECT_ID"
else
  ok "Using existing project  id=$PROJECT_ID"
fi

# ── 3. Get production environment ─────────────────────────────
step "Getting production environment"
ENVS=$(gql "{ project(id: \"$PROJECT_ID\") { environments { edges { node { id name } } } } }")
ENV_ID=$(echo "$ENVS" | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['project']['environments']['edges'][0]['node']['id'])")
ok "Environment id=$ENV_ID"

# ── 4. List existing services ─────────────────────────────────
step "Checking existing services"
SVCS=$(gql "{ project(id: \"$PROJECT_ID\") { services { edges { node { id name } } } } }")
get_svc_id() {
  echo "$SVCS" | python3 -c "
import json,sys
edges = json.load(sys.stdin)['data']['project']['services']['edges']
hit = [e['node']['id'] for e in edges if e['node']['name']=='$1']
print(hit[0] if hit else '')
"
}

# ── 5. Add PostgreSQL plugin ──────────────────────────────────
step "Adding PostgreSQL plugin"
PG_ID=$(get_svc_id "Postgres")
if [[ -z "$PG_ID" ]]; then
  PG=$(gql "mutation(\$p: String!, \$e: String!) { serviceCreate(input: { projectId: \$p, name: \"Postgres\", source: { image: \"pgvector/pgvector:pg16\" }, variables: { POSTGRES_DB: \"panoptis\", POSTGRES_USER: \"panoptis\", POSTGRES_PASSWORD: \"panoptis_secret\" } }) { id name } }" \
           "{\"p\":\"$PROJECT_ID\",\"e\":\"$ENV_ID\"}" 2>/dev/null) || true
  # Use Railway's managed Postgres plugin instead
  PG=$(gql "mutation(\$p: String!, \$e: String!) {
    postgresCreate(input: { projectId: \$p, environmentId: \$e }) { id name }
  }" "{\"p\":\"$PROJECT_ID\",\"e\":\"$ENV_ID\"}" 2>/dev/null) || true
  PG_ID=$(echo "$PG" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('postgresCreate',{}).get('id',''))" 2>/dev/null || echo "")
  [[ -n "$PG_ID" ]] && ok "Created Postgres plugin  id=$PG_ID" || info "Postgres may already exist or use dashboard to add"
else
  ok "Postgres already exists  id=$PG_ID"
fi

# ── 6. Add Redis plugin ───────────────────────────────────────
step "Adding Redis plugin"
REDIS_ID=$(get_svc_id "Redis")
if [[ -z "$REDIS_ID" ]]; then
  RD=$(gql "mutation(\$p: String!, \$e: String!) {
    redisCreate(input: { projectId: \$p, environmentId: \$e }) { id name }
  }" "{\"p\":\"$PROJECT_ID\",\"e\":\"$ENV_ID\"}" 2>/dev/null) || true
  REDIS_ID=$(echo "$RD" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('redisCreate',{}).get('id',''))" 2>/dev/null || echo "")
  [[ -n "$REDIS_ID" ]] && ok "Created Redis plugin  id=$REDIS_ID" || info "Redis may already exist or use dashboard to add"
else
  ok "Redis already exists  id=$REDIS_ID"
fi

# ── 7. Write IDs to .env for follow-on steps ──────────────────
step "Saving project IDs to .env"
python3 - <<PYEOF
import re, pathlib
env_file = pathlib.Path("$ROOT/.env")
content = env_file.read_text()

def upsert(content, key, val):
    pattern = rf'^{key}=.*$'
    line = f'{key}={val}'
    if re.search(pattern, content, re.MULTILINE):
        return re.sub(pattern, line, content, flags=re.MULTILINE)
    return content + f'\n{key}={val}'

content = upsert(content, 'RAILWAY_PROJECT_ID', '$PROJECT_ID')
content = upsert(content, 'RAILWAY_ENV_ID', '$ENV_ID')
env_file.write_text(content)
print("  .env updated")
PYEOF
ok "IDs saved"

echo ""
echo "Project:     https://railway.app/project/$PROJECT_ID"
echo ""
echo "Next step: bash scripts/deploy_services.sh"
