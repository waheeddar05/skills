# Promotion: direct ArgoCD deploy

How a build moves dev → test/QA → prod. There is **no separate promotion engine**
(no Kargo Stages, no Promotion CRs). The whole mechanism is: commit the
build-once image tag into the target env's Helm values file, and let that env's
auto-syncing ArgoCD `Application` apply it.

## The model

- The build-once artifact is the per-env dev image `…/{svc}/{svc}-dev:{run_id}`
  produced by the GitHub `deploy-dev` workflow. The **same tag** is what you put
  into test and prod values — never rebuild.
- Each env is its own ArgoCD `Application` (`{svc}-dev|test|prod`) tracking the
  `argo-deployment` branch with `syncPolicy.automated` on. The values file
  (`helm/{env}-values.yaml`) is the single source of truth for what that env
  runs.
- **Deploy = a git commit + a sync.** Nothing watches freight; nothing has to
  "advance." A commit that changes the tag is the deploy; ArgoCD applies it.

## Deploy build X to env E

1. **Edit the values file** via the GitHub Contents API on `argo-deployment`:
   - `GET /repos/{owner}/{svc}/contents/helm/{E}-values.yaml?ref=argo-deployment`
     → decode `content`, capture `sha`.
   - Replace the tag line, preserving quotes:
     `text.replace(/^(\s*)tag:\s*.*$/m, `$1tag: "${X}"`)`.
   - If the file is unchanged (already on X), **skip** — no empty commit.
   - `PUT …/contents/helm/{E}-values.yaml` with the new base64 `content`, the
     `sha`, `branch: argo-deployment`, and a message like
     `chore({E}): deploy {svc} {X}`.
2. **Nudge the app to sync now** (otherwise you wait for the poll interval):
   `kubectl annotate application {svc}-{E} -n argocd
   argocd.argoproj.io/refresh=hard --overwrite`, or via the ArgoCD/K8s API a
   merge-patch:
   `PATCH …/applications/{svc}-{E}` `{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}`
   with `Content-Type: application/merge-patch+json`. Treat a failed nudge as
   non-fatal — auto-sync still picks it up on the next cycle.
3. **Live in ~30–40s.** Confirm with the live-state read below.

Only `test-values.yaml` and `prod-values.yaml` are written this way. **Dev is
owned by the `deploy-dev` workflow** — never write `dev-values.yaml` from the
deploy path, or the two writers fight.

## Read live state from ArgoCD (never a side ledger)

Every env's truth is on its `Application`, so the deploy UI/back end reads it
directly and can't show a false "stuck":

- **Current image** — `status.summary.images` (find the `{svc}-dev:` entry, take
  the tag).
- **Health** — `status.health.status` (`Healthy`/`Progressing`/…).
- **Sync** — `status.sync.status` (`Synced`/`OutOfSync`).

`GET /apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{svc}-{E}`.

## Prod gate

Only allow a prod deploy of a build that is **currently the image running in
test/QA**: read the test app's live image and require it to equal the requested
tag before writing `prod-values.yaml`. This enforces dev → test → prod without
any stage bookkeeping.

## RBAC + permissions

- Gate the QA-deploy and prod-deploy actions behind explicit roles (e.g.
  `QA_DEPLOY` / `PROD_DEPLOY`); admins always allowed.
- The service account driving the nudge needs `get` **and** `patch` on
  `applications` in the `argocd` namespace (read for live state, patch for the
  refresh annotation). Commits use a GitHub token with contents write on the repo.

## Why not Kargo Stages (post-mortem)

The original design used a Kargo `Warehouse` + `Stage`s whose promotion ran
git-clone → yaml-update → commit → `argocd-update`. In this single-branch setup
it **deadlocks on repeat deploys**: a successful second Promotion does not
advance the Stage's `lastPromotion`, the Stage then health-checks the *old*
freight's revision against the app that already moved, goes `Unhealthy`, and
blocks the next promotion (prod gets "Freight is not available to this Stage").
`updateTargetRevision: true` fixes the app drift but **not** the advancement.
Direct-deploy removes the Stage entirely, so there is nothing to deadlock. The
Kargo `Warehouse` can stay (it's a convenient list of available build tags); the
`Stage`s are what get deleted. Full detail in `gotchas.md`.
