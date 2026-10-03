# Coworking Space Analytics Service

The analytics service is a small Flask API that reports on user activity in the
Coworking Space platform. It reads from a PostgreSQL database and exposes two
reports for business analysts:

| Endpoint | Returns |
|---|---|
| `GET /api/reports/daily_usage` | Number of check-ins per day |
| `GET /api/reports/user_visits` | Number of visits per user, with join date |
| `GET /health_check` | Liveness: the process is up |
| `GET /readiness_check` | Readiness: the app can query the `tokens` table |

This document explains how the service is built and deployed, and how to
release a change.

## How the pieces fit together

```
GitHub ──► AWS CodeBuild ──► Amazon ECR ──► Amazon EKS ──► CloudWatch
(source)   (docker build)    (image store)  (runs the pods)  (logs/metrics)
```

- **GitHub** holds the application code (`analytics/`), the build definition
  (`buildspec.yaml`), the database seed scripts (`db/`) and the Kubernetes
  manifests (`deployment/`).
- **AWS CodeBuild** builds the Docker image from `analytics/Dockerfile` and
  pushes it to ECR. Each image is tagged with the CodeBuild build number
  (`$CODEBUILD_BUILD_NUMBER`), so every build produces a new, immutable tag.
- **Amazon ECR** stores the images in the `coworking-analytics` repository.
- **Amazon EKS** runs two workloads in the `default` namespace: the analytics
  API (`coworking`) and PostgreSQL (`postgresql`).
- **CloudWatch Container Insights** collects container logs and metrics from
  the cluster through the `amazon-cloudwatch-observability` EKS add-on.

## Repository layout

| Path | Purpose |
|---|---|
| `analytics/` | Flask application, `requirements.txt` and `Dockerfile` |
| `buildspec.yaml` | CodeBuild pipeline: log in to ECR, build, tag, push |
| `db/` | SQL scripts that create and seed the `users` and `tokens` tables |
| `deployment/pv.yaml`, `pvc.yaml` | Storage for PostgreSQL |
| `deployment/postgresql-deployment.yaml`, `postgresql-service.yaml` | PostgreSQL and its in-cluster service |
| `deployment/db-configmap.yaml` | Non-secret database settings (`db-env`) |
| `deployment/db-secret.yaml` | Database credentials (`db-secret`) |
| `deployment/admin-api.yaml` | Analytics API deployment and LoadBalancer service |

## Configuration

The application is configured entirely through environment variables, so the
same image runs unchanged in any environment.

| Variable | Source in Kubernetes | Default |
|---|---|---|
| `DB_HOST` | ConfigMap `db-env` | `127.0.0.1` |
| `DB_PORT` | ConfigMap `db-env` | `5432` |
| `DB_NAME` | ConfigMap `db-env` | `postgres` |
| `DB_USERNAME` | Secret `db-secret` | required |
| `DB_PASSWORD` | Secret `db-secret` | required |
| `APP_PORT` | not set | `5153` |

`DB_HOST` points at `postgresql-service`, the ClusterIP service in front of the
database, so the API reaches PostgreSQL by service name rather than by pod IP.
The credentials in `db-secret` must match the user, password and database that
PostgreSQL was initialised with; if they drift, the readiness probe fails and
Kubernetes keeps the pod out of the service.

The CodeBuild project needs three environment variables: `AWS_ACCOUNT_ID`,
`AWS_DEFAULT_REGION` and `ECR_REPOSITORY`. It must run in privileged mode (to
run Docker) with a service role that is allowed to push to ECR.

## Deploying from scratch

1. **Database.** Apply the storage, deployment and service manifests, then load
   the seed scripts in `db/` in numeric order through a port-forward:

   ```bash
   kubectl apply -f deployment/pv.yaml -f deployment/pvc.yaml \
     -f deployment/postgresql-deployment.yaml -f deployment/postgresql-service.yaml
   kubectl port-forward svc/postgresql-service 5433:5432 &
   for f in db/*.sql; do psql -h 127.0.0.1 -p 5433 -U <DB_USER> -d <DB_NAME> < "$f"; done
   ```

2. **Image.** Run the CodeBuild project. It publishes
   `<account>.dkr.ecr.<region>.amazonaws.com/coworking-analytics:<build number>`.

3. **Application.** Set that tag in `deployment/admin-api.yaml`, then apply the
   configuration and the deployment:

   ```bash
   kubectl apply -f deployment/db-configmap.yaml -f deployment/db-secret.yaml \
     -f deployment/admin-api.yaml
   ```

4. **Verify.**

   ```bash
   kubectl get pods                      # coworking should be 1/1 Running
   kubectl get svc coworking             # note the EXTERNAL-IP
   curl <EXTERNAL-IP>:5153/api/reports/daily_usage
   ```

## Releasing a change

1. Commit the change and push it to `main`.
2. Run the CodeBuild project (or let its webhook trigger it) and note the build
   number of the successful build. That number is the new image tag.
3. Update the `image:` tag in `deployment/admin-api.yaml` and apply it:

   ```bash
   kubectl apply -f deployment/admin-api.yaml
   kubectl rollout status deployment/coworking
   ```

4. Commit the manifest so the repository always records what is running.

Kubernetes performs a rolling update: the new pod must pass `/readiness_check`
before the old one is removed, so a build that cannot reach the database never
receives traffic. To roll back, run `kubectl rollout undo deployment/coworking`
or re-apply the manifest with the previous tag.

Changes to the ConfigMap or Secret are not picked up by running pods. After
applying them, restart the deployment with
`kubectl rollout restart deployment/coworking`.

## Logs and troubleshooting

Application logs are in CloudWatch under the log group
`/aws/containerinsights/<cluster-name>/application`, in the stream for the
`coworking` pod. The same output is available directly with
`kubectl logs deploy/coworking`. The app logs the daily-usage report every
30 seconds, which makes it easy to confirm that it is healthy and connected.

| Symptom | Likely cause |
|---|---|
| Pod `0/1`, `/readiness_check` returns 500 | App cannot query the database: check `DB_*` values against PostgreSQL |
| `Connection refused` to `127.0.0.1:5432` | `DB_HOST` is not set, so the app fell back to localhost |
| `ImagePullBackOff` | The tag in `admin-api.yaml` does not exist in ECR |

## Sizing and cost

- **Instance type.** The service is a single lightweight Flask process that
  issues small SQL queries, so it is neither CPU- nor memory-intensive. A
  burstable general-purpose instance such as `t3.small` or `t3.medium` is a
  good fit: it leaves headroom for PostgreSQL and the CloudWatch agents while
  staying inexpensive.
- **Saving cost.** Keep the node group small and let it scale down when idle,
  use Spot instances for the stateless API pods, and set CPU and memory
  requests so pods pack tightly onto nodes. An ECR lifecycle policy that
  expires old image tags and a short CloudWatch log retention period keep
  storage charges from growing over time.
