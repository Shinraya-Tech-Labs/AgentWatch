#!/usr/bin/env python3
"""
AgentWatch Railway deployment script.
Uses Railway GraphQL API to create project, add plugins, and deploy services.
Run: python scripts/railway_deploy.py <RAILWAY_TOKEN>
"""

from __future__ import annotations

import json
import sys

import httpx

API = "https://backboard.railway.app/graphql/v2"


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    resp = httpx.post(
        API,
        json={"query": query, "variables": variables or {}},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


def main() -> None:
    token = sys.argv[1] if len(sys.argv) > 1 else ""
    if not token:
        print("Usage: python scripts/railway_deploy.py <RAILWAY_TOKEN>")
        sys.exit(1)

    # Verify token
    try:
        me = gql(token, "{ me { name email } }")
        print(f"Logged in as: {me['me']['name']} ({me['me']['email']})")
    except Exception as exc:
        print(f"Token invalid: {exc}")
        sys.exit(1)

    # List existing projects
    projects = gql(token, "{ me { projects { edges { node { id name } } } } }")
    existing = {p["node"]["name"]: p["node"]["id"] for p in projects["me"]["projects"]["edges"]}
    print(f"Existing projects: {list(existing.keys()) or 'none'}")

    project_name = "panoptis"
    if project_name in existing:
        project_id = existing[project_name]
        print(f"Using existing project: {project_id}")
    else:
        result = gql(
            token,
            """
            mutation($name: String!) {
                projectCreate(input: { name: $name, isPublic: false }) { id name }
            }
        """,
            {"name": project_name},
        )
        project_id = result["projectCreate"]["id"]
        print(f"Created project: {project_id}")

    # Get environments
    envs = gql(
        token,
        """
        query($id: String!) {
            project(id: $id) { environments { edges { node { id name } } } }
        }
    """,
        {"id": project_id},
    )
    env_id = envs["project"]["environments"]["edges"][0]["node"]["id"]
    print(f"Environment: {env_id}")

    # List services
    services_data = gql(
        token,
        """
        query($id: String!) {
            project(id: $id) { services { edges { node { id name } } } }
        }
    """,
        {"id": project_id},
    )
    svc_map = {
        s["node"]["name"]: s["node"]["id"] for s in services_data["project"]["services"]["edges"]
    }
    print(f"Existing services: {list(svc_map.keys()) or 'none'}")

    print("\nAll pre-checks done. Project and environment ready.")
    print(f"Project ID:     {project_id}")
    print(f"Environment ID: {env_id}")
    print("\nNext: use `railway link` + `railway up` to deploy each service.")
    print("Or paste these IDs into the Railway dashboard to finish deployment.")

    # Write IDs to a local file for follow-up steps
    with open("railway_ids.json", "w") as f:
        json.dump({"project_id": project_id, "env_id": env_id, "services": svc_map}, f, indent=2)
    print("\nWrote railway_ids.json")


if __name__ == "__main__":
    main()
