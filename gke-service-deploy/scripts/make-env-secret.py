#!/usr/bin/env python3
"""Clone a service's K8s secret into a new environment with env-specific overrides.

Clones <src-secret> (e.g. <svc>-secret-dev) into <dst-secret> in the target
namespace/cluster, overriding only env-specific keys. Secret values are never
printed. Pass DB overrides only if the service uses a database.

Usage:
  make-env-secret.py \
    --src-ctx <kctx> --src-ns dev-saras --src-secret <svc>-secret-dev \
    --dst-ctx <kctx> --dst-ns test-saras --dst-secret <svc>-secret-test \
    --base-url https://test-<svc>.sarasanalytics.com \
    --active false \                     # active/passive flag value (key via --active-key)
    --active-key CLICKUP_WEBHOOK_ENABLED \
    [--db-name <svc>_test --db-user <svc> --db-password <pw>]   # if DB

Tip: generate the DB password upstream and pass it here so it never hits a shell
history or chat; this script puts it only into the K8s secret.
"""
import argparse, base64, json, re, subprocess, sys

def b64(s): return base64.b64encode(s.encode()).decode()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src-ctx", required=True); p.add_argument("--src-ns", required=True)
    p.add_argument("--src-secret", required=True)
    p.add_argument("--dst-ctx", required=True); p.add_argument("--dst-ns", required=True)
    p.add_argument("--dst-secret", required=True)
    p.add_argument("--base-url")
    p.add_argument("--active-key"); p.add_argument("--active")
    p.add_argument("--db-name"); p.add_argument("--db-user"); p.add_argument("--db-password")
    a = p.parse_args()

    raw = subprocess.check_output(["kubectl", "--context", a.src_ctx, "get", "secret",
                                   a.src_secret, "-n", a.src_ns, "-o", "json"])
    sec = json.loads(raw)
    data = dict(sec["data"])

    def dec(k): return base64.b64decode(data[k]).decode() if k in data else None

    # Database overrides (only if the service uses a DB).
    if a.db_password and a.db_user and "DATABASE_URL" in data:
        dburl = dec("DATABASE_URL")
        m = re.match(r'^(postgresql://)[^:]+:[^@]+@([^/]+)/[^?]+(.*)$', dburl)
        db = a.db_name or (a.db_user)
        data["DATABASE_URL"] = b64(f"{m.group(1)}{a.db_user}:{a.db_password}@{m.group(2)}/{db}{m.group(3)}"
                                   if m else f"postgresql://{a.db_user}:{a.db_password}@127.0.0.1:5432/{db}")
        if "DB_PASSWORD" in data: data["DB_PASSWORD"] = b64(a.db_password)
        if "DB_USER" in data:     data["DB_USER"] = b64(a.db_user)
        if a.db_name and "DB_NAME" in data: data["DB_NAME"] = b64(a.db_name)

    if a.base_url:
        if "BASE_URL" in data:        data["BASE_URL"] = b64(a.base_url)
        if "PUBLIC_BASE_URL" in data: data["PUBLIC_BASE_URL"] = b64(a.base_url)
    if a.active_key and a.active is not None:
        data[a.active_key] = b64(a.active)   # "false" => passive/standby for a new env

    newsec = {"apiVersion": "v1", "kind": "Secret", "type": sec.get("type", "Opaque"),
              "metadata": {"name": a.dst_secret, "namespace": a.dst_ns}, "data": data}
    tmp = "/tmp/_env_secret.json"
    open(tmp, "w").write(json.dumps(newsec))
    r = subprocess.run(["kubectl", "--context", a.dst_ctx, "apply", "-f", tmp])
    subprocess.run(["rm", "-f", tmp])
    print(f"{'applied' if r.returncode==0 else 'FAILED'} {a.dst_secret} -> {a.dst_ns} | keys={len(data)}")
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
