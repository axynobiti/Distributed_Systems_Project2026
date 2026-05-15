# Distributed Systems Project 2026

## Prerequisites

Install Minikube first:

```bash
curl -LO https://github.com/kubernetes/minikube/releases/latest/download/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
rm minikube-linux-amd64
```

Verify the installation:

```bash
minikube version
```

Install Docker as the Minikube driver:

```bash
sudo apt update
sudo apt install -y docker.io
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

Start Minikube with Docker:

```bash
minikube start --driver=docker
```

Install kubectl:

```bash
sudo snap install kubectl --classic
```

Verify kubectl:

```bash
kubectl version --client
```

Connect kubectl to Minikube:

```bash
minikube status
kubectl config use-context minikube
```

## Start the Kubernetes cluster

The project can be started on Minikube with:

```bash
make cluster-ready
```

This command:

- starts Minikube
- builds the auth, manager, UI, and worker Docker images
- loads the images into Minikube
- deploys PostgreSQL, MinIO, auth, manager, and UI
- seeds the default admin user
- waits until the cluster is ready

To use a custom image tag:

```bash
make cluster-ready TAG=v1
```

## Check the deployment

```bash
make status
```

or:

```bash
kubectl get pods -n distributed-systems-project2026
kubectl get svc -n distributed-systems-project2026
```

## Redeploy after code changes

For UI changes:

```bash
make redeploy-ui
```

For manager changes:

```bash
make redeploy-manager
```

For worker changes:

```bash
make rebuild-worker
```

Then submit a new job from the CLI.

## Clean up

Delete only this project's Kubernetes namespace:

```bash
make clean-cluster
```

Delete the whole Minikube cluster:

```bash
make minikube-delete
```

## Use the CLI

The default admin user is:

```text
username: admin
password: admin123
```

For Minikube NodePort access, use:

```bash
UI_SERVICE_URL=http://$(minikube ip):30080 python3 cli.py login --username admin --password admin123
```

If `cli.py` already points to the correct UI URL, you can run:

```bash
python3 cli.py login --username admin --password admin123
python3 cli.py admin list-users
python3 cli.py jobs list
```

## How to run a MapReduce job

This section explains how to run the two example MapReduce jobs in
`test-files/`: WordCount and WordCo-occurrence.

### 1. Log in

The default admin user is:

```text
username: admin
password: admin123
```

Log in through the UI service:

```bash
UI_SERVICE_URL=http://$(minikube ip):30080 python3 cli.py login --username admin --password admin123
```

### 2. Run WordCount

The WordCount example uses:

```text
test-files/WordCount/wordcount_input.txt
test-files/WordCount/wordcount_mapper.py
test-files/WordCount/wordcount_reducer.py
```

Submit the job:

```bash
python3 cli.py jobs submit \
  --input test-files/WordCount/wordcount_input.txt \
  --mapper test-files/WordCount/wordcount_mapper.py \
  --reducer test-files/WordCount/wordcount_reducer.py
```

Use the returned job id to check status:

```bash
python3 cli.py jobs view --job-id <job-id>
```

After the job status becomes `completed`, retrieve the result:

```bash
python3 cli.py jobs retrieve result --job-id <job-id>
```

### 3. Run WordCo-occurrence

The WordCo-occurrence example uses:

```text
test-files/WordCo-occurrence/input.txt
test-files/WordCo-occurrence/mapper.py
test-files/WordCo-occurrence/reducer.py
```

Submit the job:

```bash
python3 cli.py jobs submit \
  --input test-files/WordCo-occurrence/input.txt \
  --mapper test-files/WordCo-occurrence/mapper.py \
  --reducer test-files/WordCo-occurrence/reducer.py
```

Use the returned job id to check status:

```bash
python3 cli.py jobs view --job-id <job-id>
```

After the job status becomes `completed`, retrieve the result:

```bash
python3 cli.py jobs retrieve result --job-id <job-id>
```