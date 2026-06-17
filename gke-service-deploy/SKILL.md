---
name: gke-service-deploy
description: >-
  Deploy a Saras Analytics service to the GKE clusters (dev, test/QA, prod) from
  its GitHub repo using the build-once + ArgoCD + Kargo artifact-promotion
  pattern. Use this whenever the user wants to deploy, ship, stand up, onboard,
  or roll out a service/repo to the Saras GKE infra, set up its CI/CD, add it to
  ArgoCD or Kargo, create per-env Cloud SQL databases for it, or promote a build
  dev→test→prod — e.g. "deploy https://github.com/sarasanalytics-com/<svc> to
  dev", "stand up <svc> on test/QA", "ship this service to prod", "promote the
  latest build to test", "add a Kargo stage for <svc>". Pass a GitHub repo URL
  and it deploys (or promotes) the service. Also trigger on mentions of
  build-once image promotion, autoship/saras-ai-gateway-style deployment, the
  argo-deployment branch, dev-saras/test-saras/saras namespaces, or the
  dev→test→prod promotion flow, even if "skill" isn't said.
---

# Deploy a service to Saras GKE (build-once + ArgoCD + Kargo)

This skill deploys a service from a GitHub repo onto the Saras Analytics GKE
clusters and wires up promotion. The model is **build the image once, then
promote that same artifact across environments** — never rebuild per env. Each
environment (dev, test/QA, prod) is its own ArgoCD `Application` reading a
per-env Helm values file off the repo's `argo-deployment` branch; Kargo handles
dev→test→prod promotion of the single image.

Read `references/infra.md` for the concrete cluster/registry/DB/DNS facts and
`references/gotchas.md` for the failure modes that will bite you — both are
load-bearing, not optional reading.

## Inputs

- **GitHub repo URL** (required) — e.g. `https://github.com/sarasanalytics-com/<svc>`.
- **Target env(s)** (optional) — `dev`, `test`, `prod`. Default: **dev first**, then promote. Never stand all three up in one shot (see Safety).
- **Service name** (optional) — defaults to the repo name. Becomes the ArgoCD app prefix (`<svc>-dev`), namespace member, image repo, and host.
- **Needs a database?** (optional) — many services do; detect from `.env.example`/code.
- **Has live side effects?** (optional) — if the service processes webhooks, runs schedulers, opens PRs, sends messages, etc., it needs the **active/passive toggle** (see below) so two environments don't both fire.

## Prerequisites

`gh` (authed), `kubectl` (all three GKE contexts), `gcloud` + `gke-gcloud-auth-plugin`
(`export USE_GKE_GCLOUD_AUTH_PLUGIN=True`), and `helm`. Confirm contexts with
`kubectl config get-contexts`. The user's workspace path may contain a comma —
`kubectl -f` splits on commas, so apply manifests from a comma-free path.

## Workflow

### 1. Inspect the repo (decide the shape before touching infra)

Clone/read the repo and answer:
- **How is it built?** Look at `.github/workflows/*.yml`. Most Saras services build on push to `dev` to a per-env image `…/<svc>/<svc>-dev:<github.run_id>` and `sed` the tag into `helm/dev-values.yaml` on the `argo-deployment` branch. That per-env image **is** your build-once artifact — reuse the exact tag/digest for test and prod; do not rebuild.
- **Where's the chart?** Usually an **orphan `argo-deployment` branch** with `helm/{Chart.yaml,<env>-values.yaml,templates/}`. If only `dev-values.yaml` exists, you'll add `test-values.yaml` / `prod-values.yaml`.
- **Does it need a DB?** Check `.env.example` for `DATABASE_URL`/`DB_*` and a `cloudSqlProxy` block in the chart.
- **Does it have side effects?** Webhooks, pollers, schedulers, agents that write to external systems → it needs an active/passive switch (see step below). If the app already has one (a config flag), reuse it; if not, that's a code change (PR to `dev`) before standing up a second live environment.

### 2. Stand up the target environment

Mirror the dev deployment for the new env. For each env use the right cluster,
namespace, ArgoCD project, Cloud SQL instance, host, and DNS target from
`references/infra.md`. Concretely:

1. **Image** — reuse the build-once tag. Confirm it exists in Artifact Registry. Don't build a new image for test/prod.
2. **Database** (if needed) — create an empty DB and a **BUILT_IN** user on the env's Cloud SQL instance:
   `gcloud sql databases create <db> --instance=<instance>` and
   `gcloud sql users create <svc> --instance=<instance> --password=<generated>`.
   BUILT_IN users are auto-granted `cloudsqlsuperuser`, so the app can bootstrap its own schema. (See gotchas for the IAM-vs-password and schema-permission details.)
3. **Secret** — clone the dev secret and override only env-specific keys (DB URL/password/name, `BASE_URL`/`PUBLIC_BASE_URL` to the env host, and the active/passive flag → **false** for a new env). Use `scripts/make-env-secret.py` as a starting point; never print secret values.
4. **Values** — write `helm/<env>-values.yaml` (template in `templates/values.yaml.tmpl`): image repo+tag, `cloudSqlProxy.instanceConnectionName` for the env instance, `ingress.host`, `secretRef`, and a pod-anti-affinity keyed to `<svc>-<env>`. Commit it to the `argo-deployment` branch (the existing `deploy-dev` flow only touches `dev-values.yaml`, so committing other env values doesn't collide).
5. **ArgoCD app** — create `<svc>-<env>` (template in `templates/argocd-app.yaml.tmpl`): the env's `project`, destination `server`+`namespace`, source `path: helm` + `valueFiles: [<env>-values.yaml]` on `argo-deployment`, `syncPolicy.automated.selfHeal: true` + `CreateNamespace=true`. Apply to the **argocd** cluster.
6. **Ingress** — the chart's ingress template creates it from `ingress.host`. Confirm it appears.
7. **DNS** — add a Cloudflare A record for the host → the env's **public** DNS target (not the in-cluster Traefik IP). See gotchas for the exact targets and the API method (the dashboard SPA is unreliable under automation; use the token-authenticated API with the `X-Cross-Site-Security: dash` header in the user's logged-in session).
8. **OAuth redirect** (if the app has Google/SSO login) — the env host needs `https://<host>/auth/google/callback` added to the OAuth client's authorized redirect URIs. **This is a security setting — have the user add it; don't modify it yourself.**

### 3. Wire Kargo promotion

Set up (or extend) the service's Kargo Project so the build-once artifact
promotes across envs. Templates in `templates/kargo.yaml.tmpl`. Key points:
- **Warehouse** watches the image repo (numeric run-id tags → `allowTags: "^[0-9]+$"`, `imageSelectionStrategy: NewestBuild`, `strictSemvers: false`).
- **Stages** promote the freight: each stage's promotion does git-clone → `yaml-update` the env values tag → git-commit → git-push → `argocd-update` the env app. Authorize each stage on its app with annotation `kargo.akuity.io/authorized-stage: <project>:<stage>`.
- **Do NOT create a Kargo stage that writes `dev-values.yaml`** — the GitHub `deploy-dev` workflow already owns that file, and a dual writer is what broke the original pilot. Kargo manages test/prod values; dev stays on its workflow.
- Order stages so prod sources from test (`sources.stages: [test]`) to enforce dev→test→prod.
- Promotions are **manual** by default (no auto-promotion policy) so the user gates each move. Credentials + an RBAC/restart gotcha are covered in `references/gotchas.md`.

### 4. Validate

Confirm the pod is `Running` (2/2 with the proxy), `/health` returns
`db: connected`, logs show schema init and the expected active/passive line, and
the public URL responds end-to-end (DNS → LB → Traefik → pod). For prod,
validate while still passive before any activation.

## The active/passive toggle (for side-effecting services)

A service that opens PRs, posts messages, polls, or runs schedulers must not run
"live" in two environments at once, or you get duplicate side effects. The
pattern: a single config flag (e.g. `CLICKUP_WEBHOOK_ENABLED`) that, when
**false**, puts the instance in passive/standby — it serves health/dashboard but
ignores webhooks, skips pollers/schedulers, and doesn't start side-effecting
background loops. New environments come up **passive**; you flip exactly one
environment **active** (and move the external webhook to it) as a deliberate
cutover, not as part of the deploy. If the repo lacks such a flag, add it
(small PR to `dev`) before standing up a second live env.

## Safety / decision points

- **Dev first, then promote.** Don't cut over all environments at once. Validate each before the next.
- **Prod is special.** Confirm with the user before deploying prod, and default prod to **passive standby** — deployed and healthy but not the active receiver — until they deliberately activate it. Watch for prod-only hazards in the repo (e.g. an ORM auto-migrating the prod schema).
- **Copying data into an environment?** If you seed a new env's DB from another env, neutralize any in-flight/non-terminal work the app would auto-resume on boot, so the new instance doesn't re-run the source env's tasks. See gotchas.
- **Security settings stay with the user** — OAuth redirect URIs, IAM/role changes, and the like. Surface the exact change; let them apply it.

## Pointers

- `references/infra.md` — clusters, projects, registries, Cloud SQL instances, namespaces, ArgoCD projects, ingress + DNS targets, host/naming conventions, Kargo location.
- `references/gotchas.md` — the failure modes (Kargo RBAC + controller restart, Postgres schema perms, Cloud SQL import ownership, proxy auth, Cloudflare API quirks, env-naming footgun, and more).
- `templates/` — `values.yaml.tmpl`, `argocd-app.yaml.tmpl`, `kargo.yaml.tmpl`.
- `scripts/make-env-secret.py` — clone a secret to a new env with overrides, without printing values.
