# Gotchas (hard-won)

Things that will silently break a deploy. Each cost real debugging time.

## Kargo

1. **Warehouse can't read its creds → fix RBAC, then restart the controller.**
   A fresh Kargo `Project` namespace is missing the `kargo-controller-read-secrets`
   RoleBinding that an existing project has. Symptom: Warehouse `DiscoveryFailure`
   = `secrets is forbidden: User "system:serviceaccount:kargo:kargo-controller"
   cannot list resource "secrets"`. Fix:
   `kubectl create rolebinding kargo-controller-read-secrets -n <project> --clusterrole=kargo-controller-read-secrets --serviceaccount=kargo:kargo-controller`.
   Then — and this is the part that's easy to miss — the controller's cached
   informer won't pick up the new permission until you
   `kubectl -n kargo rollout restart deploy/kargo-controller`. After that,
   discovery succeeds and Freight appears.

2. **Dual-writer on `dev-values.yaml`.** The GitHub `deploy-dev` workflow `sed`s
   the dev tag into `helm/dev-values.yaml`. Do not also create a Kargo stage
   that writes that file — they fight. Kargo owns `test-values.yaml` /
   `prod-values.yaml`; dev stays on the workflow.

3. **Copy the credential secrets per project.** `kargo-git-creds` /
   `kargo-ar-creds` are matched by `repoURL`. Reuse the same token/SA-key bytes
   but set the new project's `repoURL` (the service's git repo + image repo).

4. Promotion names must be RFC-1123 (`[a-z0-9-]`, no dots). Manual `Promotion`
   CRs need the steps inlined (the Stage's `promotionTemplate` is only used by
   auto-promotion). Argo Rollouts CRDs must exist before the Kargo controller
   starts, or it crashloops.

## Postgres / Cloud SQL

5. **Schema bootstrap permissions (PG15+).** On a fresh DB, a plain role often
   can't `CREATE` in the `public` schema. Avoid this by creating the app user as
   a **BUILT_IN** user via `gcloud sql users create` — Cloud SQL auto-grants it
   `cloudsqlsuperuser`, so the app bootstraps its own schema. (Empirically the
   app's `CREATE TABLE IF NOT EXISTS` startup just works with a BUILT_IN user.)

6. **`gcloud sql import` runs as one transaction → a late error rolls back
   everything.** Importing a `pg_dump` produced by `gcloud sql export` can build
   every table and copy all rows, then fail on the final `ALTER DEFAULT
   PRIVILEGES` ("permission denied to change default privileges") and **roll the
   whole import back** — leaving the DB empty despite a "DONE" op. Fix: run the
   import as the object owner, `gcloud sql import sql … --user=<svc>`. The owner
   can set its own default privileges, so the import commits.

7. **Recreate the target DB empty before importing** (scale the consumer to 0
   first; ArgoCD `selfHeal` will fight a `kubectl scale`, so temporarily
   `kubectl patch application <app> -n argocd --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`,
   scale to 0, import, then restore the syncPolicy). A dump from `gcloud sql
   export` has no `--clean`, so importing onto an existing schema errors.

8. **Seeding a DB into an *active* instance can resume the source's work.** On
   boot the app may auto-recover in-flight tasks (e.g. re-execute `approved`
   rows). Before bringing the consumer up on copied data, neutralize non-terminal
   work, e.g. `UPDATE tasks SET state='failed' WHERE state='approved' AND
   started_at IS NULL;` (run via a tiny `gcloud sql import` SQL file as the
   owner). Otherwise the new env opens duplicate PRs / re-runs jobs.

## Cloudflare DNS

9. **The dashboard SPA is unreliable under automation** (it can hang on its
   loader, and the extension may be bound to a *different* Chrome profile that
   isn't logged in). Don't fight it. Instead drive the **API** from the user's
   logged-in session:
   - `POST https://dash.cloudflare.com/api/v4/zones/<zoneId>/dns_records` with a
     synchronous same-origin `XMLHttpRequest` and the header
     **`X-Cross-Site-Security: dash`** (without it you get a 403 WAF challenge).
   - Body: `{type:"A", name:"<host-label>", content:"<LB IP>", ttl:1, proxied:false}`.
   - If a request returns `9300 "User session has expired"`, the user must
     re-log-in to `dash.cloudflare.com` (this is separate from any Google login).
   - The DNS A target is the env's public LB IP (see infra.md), not the in-cluster
     Traefik IP.

10. **DNS-only (grey cloud)** — proxied records break the GCP-managed cert / LB
    routing for these hosts.

## Misc

11. **`kubectl -f` splits on commas.** If the working path contains a comma
    (e.g. `coding, software development`), apply manifests from a comma-free dir.

12. **OAuth `redirect_uri_mismatch`** on the dashboard login means the env host's
    `https://<host>/auth/google/callback` isn't on the OAuth client's authorized
    redirect URIs. That's a security setting — have the user add it; don't edit
    OAuth/IAM config yourself.

13. **Don't print secrets.** When cloning secrets or generating DB passwords,
    keep values server-side (build the K8s secret in a script, write any
    human-needed credential to a `chmod 600` file, never to chat).
