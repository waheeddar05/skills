# Saras GKE infrastructure reference

Concrete facts for `gke-service-deploy`. Verify with `kubectl`/`gcloud` before
acting — values drift over time — but these are the current conventions.

## GCP project

`daton-210514` (codename "daton" from the legacy product).

## Clusters (all `us-central1-c`)

| Role | kubectl context | In-cluster API server | Notes |
|------|-----------------|-----------------------|-------|
| ArgoCD + Kargo control plane | `gke_daton-210514_us-central1-c_k8s-cluster-argocd-daton-1` | n/a (you target it directly) | Runs ArgoCD, Kargo, cert-manager, Argo Rollouts |
| dev + test | `gke_daton-210514_us-central1-c_k8s-dev-test-cluster-daton` | `https://34.72.110.156` | Hosts both dev and test envs |
| prod | `gke_daton-210514_us-central1-c_k8s-production-cluster-daton` | `https://34.42.255.233` | prod only |

ArgoCD `Application`s live on the **argocd** cluster (namespace `argocd`); their
`destination.server` points at the dev-test or prod API server above.

## Per-environment map

| Env | Namespace | ArgoCD project | Cloud SQL instance | Host convention | DNS A target |
|-----|-----------|----------------|--------------------|-----------------|--------------|
| dev | `dev-saras` | `dev-daton` | `daton-210514:us-central1:saras-dev-test-rw-3` | `dev-{svc}.sarasanalytics.com` | `34.120.254.30` |
| test/QA | `test-saras` | `test-daton` | `daton-210514:us-central1:saras-dev-test-rw-3` | `test-{svc}.sarasanalytics.com` | `34.120.254.30` |
| prod | `saras` | `prod` | `daton-210514:us-central1:saras-prod-subscriptions-1` (or the service's own prod instance) | `{svc}.sarasanalytics.com` (no prefix) | `34.111.60.117` |

> **Env-naming footgun:** code branches are `main`, `dev`, `qa`, `prod`, but the
> `qa` branch builds the **test** environment. Apps, values files, namespaces,
> and image repos all use `dev`/`test`/`prod`. "QA" == "test".

The DNS A target is the **public GCP load-balancer** IP that fronts Traefik —
**not** the `ADDRESS` shown on the in-cluster ingress (that's Traefik's internal
service IP). Confirm by digging an existing host in the same env, e.g.
`dig +short test-{existing}.sarasanalytics.com` or
`dig +short cstudio.sarasanalytics.com` (prod).

## Container images (build-once)

Registry pattern: `us-central1-docker.pkg.dev/daton-210514/{svc}/{svc}-dev:{github.run_id}`.
Despite the `-dev` suffix in the repo name, the **same image** is promoted to
test and prod — that's the build-once artifact. Create the AR repo if missing:
`gcloud artifacts repositories create {svc} --repository-format=docker --location=us-central1 --project=daton-210514`.
Build `--platform linux/amd64` (GKE nodes are amd64).

`imagePullSecrets: [{name: gcr-image-pull}]` — this secret already exists in
`dev-saras`, `test-saras`, and `saras`.

## Cloud SQL

- Connect via the **Cloud SQL Auth Proxy** sidecar (chart `cloudSqlProxy` block).
  The chart hardcodes `--auto-iam-authn`; the app still authenticates as a
  **BUILT_IN** Postgres user with a password from the secret. Both layers
  coexist: the proxy uses the pod's IAM identity for the instance connection,
  the app uses user/password for the Postgres session.
- Pods use the namespace `default` KSA, Workload-Identity-bound to GSA
  `daton-cloud-sql@daton-210514.iam.gserviceaccount.com` (same on dev-test and
  prod), which has Cloud SQL Client across the project — so a pod can reach any
  instance in `daton-210514`.
- `DATABASE_URL` is `postgresql://{svc}:{password}@127.0.0.1:5432/{db}` (host is
  localhost via the proxy; the *instance* is set in values, not the URL).

## Ingress / DNS

- Ingress class `traefik`, behind a GCP global LB, wildcard cert
  `*.sarasanalytics.com` (so any `{host}.sarasanalytics.com` is TLS-covered).
- DNS is **Cloudflare**, zone `sarasanalytics.com` (zone id
  `c5ab6ed2696edd818e576fb613ba18d6`), authoritative NS `amos.ns.cloudflare.com`
  / `kiki.ns.cloudflare.com`. Records are **DNS-only** (grey cloud) — they point
  straight at the origin LB; do not proxy them.

## Kargo (build list only — promotion is direct; see promotion.md)

> Kargo is still installed, but this skill **no longer uses Kargo Stages** for
> promotion — they deadlock on repeat deploys (see gotchas.md), so deploys now go
> direct (commit tag + ArgoCD auto-sync). A `Warehouse` may stay on purely as a
> list of available build tags; everything below applies only to that optional use.

Installed on the **argocd** cluster (namespace `kargo`), v1.10.x. Per-service
`Project` = a namespace named after the project. Credentials live as labeled
secrets in that namespace: `kargo-git-creds` (label
`kargo.akuity.io/cred-type: git`) and `kargo-ar-creds`
(`kargo.akuity.io/cred-type: image`), each with `repoURL`/`username`/`password`.
The `kargo-api` UI is `ClusterIP` (no ingress) — reach it via
`kubectl -n kargo port-forward svc/kargo-api 8443:443` and log in as the local
`admin` account. There is no SSO.
