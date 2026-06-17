# skills

A collection of Claude/Cowork **skills**. Each skill lives in its own folder
with a `SKILL.md` (name + description + instructions) plus any `references/`,
`templates/`, and `scripts/` it needs.

## Skills

### [`gke-service-deploy/`](./gke-service-deploy/)
Deploy a Saras Analytics service to the GKE clusters (dev, test/QA, prod) from
its GitHub repo using the build-once + ArgoCD + Kargo artifact-promotion
pattern. Pass a GitHub repo URL and it deploys (or promotes) the service —
per-env Cloud SQL DBs, secrets, Helm values, ArgoCD apps, ingress/DNS, Kargo
stages, and the active/passive toggle for side-effecting services. Encodes the
real infra facts and the failure modes learned standing up `autoship`.

### [`claude-max-proxy/`](./claude-max-proxy/)
Route OpenClaw through a Claude Max/Pro/Team subscription via the
claude-max-api-proxy (wraps Claude Code CLI auth as an OpenAI-compatible
endpoint). _(Moved here from the repo root when this became a multi-skill repo;
git history is preserved.)_

## Using a skill

Install a skill into Cowork/Claude via **Settings → Capabilities** (point it at
the skill folder, or install a packaged `.skill` bundle). A skill is just a
folder of instructions — Claude reads `SKILL.md` when the task matches its
description and follows it.
