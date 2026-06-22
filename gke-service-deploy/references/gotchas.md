# Gotchas (hard-won)

Things that will silently break a deploy. Each cost real debugging time.

## Promotion / deploy

1. **The Kargo-Stage deadlock — why this skill uses direct deploy, not Stages.**
   The original design promoted with a Kargo `Warehouse` + `Stage`s
   (git-clone → yaml-update → commit → `argocd-update`). In this single-branch
   setup it **deadlocks on repeat deploys**: a *successful* second Promotion
   does not advance the Stage's `lastPromotion`; the Stage then health-checks the
   **old** freight's git revision against the app that has already moved, reports
   `Unhealthy`, and blocks the next promotion. Symptom at the UI: a deploy that
   actually applied still looks "stuck," and prod is rejected with *"Freight is
   not available to this Stage."* Adding `updateTargetRevision: true` to the
   stage's `argocd-update` pins the app revision and fixes the *drift* but **not**
   the advancement. The durable fix is to drop Stages entirely and deploy
   directly (see `promotion.md`): commit the tag into `{env}-values.yaml` and let
   the env's auto-syncing ArgoCD app apply it. A Kargo `Warehouse` may stay on
   purely as a list of available build tags; the `Stage`s are deleted.

2. **Dual-writer on `dev-values.yaml`.** The GitHub `deploy-dev` workflow `sed`s
   the dev tag into `helm/dev-values.yaml`. The deploy path must **only** write
   `test-values.yaml` / `prod-values.yaml` — never dev. Two writers on the same
   file fight and was what broke the original pilot.

3. **Keep the image tag quoted in `*-values.yaml`.** Numeric `github.run_id`
   tags (e.g. `27973503275`) are valid YAML numbers; written unquoted — or
   serialized by a YAML library as a float like `2.7730294248e+10` — ArgoCD/k8s
   reject the image as `InvalidImageName`. Always write `tag: "{run_id}"`, and
   when editing programmatically replace the value **preserving the quotes**
   (a regex on the `tag:` line, not a YAML round-trip that may re-type it).

4. **ArgoCD nudge needs `get`+`patch` on Applications.** The deploy reads live
   env state from each `Application` and patches the `argocd.argoproj.io/refresh:
   hard` annotation to sync immediately. The driving service account therefore
   needs both `get` and `patch` on `applications` in the `argocd` namespace; a
   read-only binding makes deploys silently wait for the poll interval.

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
   import as the object owner, `gcloud sql import sql … --user={svc}`. The owner
   can set its own default privileges, so the import commits.

7. **Recreate the target DB empty before importing** (scale the consumer to 0
   first; ArgoCD `selfHeal` will fight a `kubectl scale`, so temporarily
   `kubectl patch application {app} -n argocd --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`,
   scale to 0, import, then restore the syncPolicy). A dump from `gcloud sql
   export` has no `--clean`, so importing onto an existing schema errors.

8. **Seeding a DB into an *active* instance can resume the source's work.** On
   boot the app may auto-recover in-flight tasks (e.g. re-execute `approved`
   rows). Before bringing the consumer up on copied data, neutralize non-terminal
   work, e.g. `UPDATE tasks SET state='failed' WHERE state='approved' AND
   started_at IS NULL;` (run via a tiny `gcloud sql import` SQL file as the
   owner). Otherwise the new env opens duplicate PRs / re-runs jobs.

## Secondary database connection (second proxy sidecar)

9. **A second DB on a different instance = a second cloud-sql-proxy sidecar.**
   Some services hold two connections — the app's own DB *and* a shared/read DB
   on another Cloud SQL instance (e.g. a CDK error-listener reading
   `daton_webapp`). The second connection needs its **own** `cloud-sql-proxy`
   container in the pod pointing at that instance, its own `*_DATABASE_URL`
   secret key, and credentials for that instance. If the instance requires SSL
   (`TRUSTED_CLIENT_CERTIFICATE_REQUIRED` / `requireSsl`), you must go through
   the proxy — a direct `host:5432` URL will be refused. Use Workload Identity
   where the GSA has access, otherwise mount a SA-key file (`--credentials-file`)
   for that instance. Pod goes 2/2 → 3/3 with the extra proxy.

10. **An explicit `env:` var overrides `envFrom` secret values (k8s precedence).**
    A hardcoded `SARAS_DATABASE_URL` (or any key) set as a literal `env:` entry
    on the Deployment **shadows** the same key coming from the secret via
    `envFrom` — the app connects to the wrong/old DB and you chase a phantom
    "database does not exist." Remove the explicit override
    (`kubectl set env deploy/{app} -c {container} SARAS_DATABASE_URL-`) so the
    secret value wins.

## Cloudflare DNS

11. **The dashboard SPA is unreliable under automation** (it can hang on its
    loader, and the extension may be bound to a *different* Chrome profile that
    isn't logged in). Don't fight it. Instead drive the **API** from the user's
    logged-in session:
    - `POST https://dash.cloudflare.com/api/v4/zones/{zoneId}/dns_records` with a
      synchronous same-origin `XMLHttpRequest` and the header
      **`X-Cross-Site-Security: dash`** (without it you get a 403 WAF challenge).
    - Body: `{type:"A", name:"{host-label}", content:"{LB IP}", ttl:1, proxied:false}`.
    - If a request returns `9300 "User session has expired"`, the user must
      re-log-in to `dash.cloudflare.com` (separate from any Google login).
    - The DNS A target is the env's public LB IP (see infra.md), not the
      in-cluster Traefik IP.

12. **DNS-only (grey cloud)** — proxied records break the GCP-managed cert / LB
    routing for these hosts.

## Misc

13. **`kubectl -f` splits on commas.** If the working path contains a comma
    (e.g. `coding, software development`), apply manifests from a comma-free dir.

14. **OAuth `redirect_uri_mismatch`** on the dashboard login means the env host's
    `https://{host}/auth/google/callback` isn't on the OAuth client's authorized
    redirect URIs. That's a security setting — have the user add it; don't edit
    OAuth/IAM config yourself.

15. **Don't print secrets.** When cloning secrets, generating DB passwords, or
    handling a pasted SA key / DB password, keep values server-side (build the
    K8s secret in a script, write any human-needed credential to a `chmod 600`
    file, never to chat).
