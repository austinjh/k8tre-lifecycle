# k8tre-lifecycle

Supporting services for the K8TRE research project lifecycle prototype.

This repository starts with deployment glue for the lifecycle target VM. The API, portal extensions and evaluation material will be added in later milestones.

## Contents

- `manifests/object-storage` — SeaweedFS overlay using `rwo-default` instead of upstream Longhorn, with S3 access restricted to project, `lifecycle` and `backend` namespaces.
- `argo/lifecycle-addons.yaml` — Argo CD Application for the overlay.

The baseline K8TRE VM is frozen separately in the `k8tre` repository; this repository is only deployed to `k8tre-lifecycle-node` / `dev2.k8tre.internal`.
