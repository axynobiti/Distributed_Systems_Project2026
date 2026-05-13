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

## How to run a test MapReduce job

This section explains how to run a simple word-count MapReduce job using the CLI.

### 1. Create the test files

From the project root:

```bash
cd ~/Distributed_Systems_Project2026
mkdir -p mr-test
```

Create the input file:

```bash
printf "hello world\nhello mapreduce\n" > mr-test/input.txt
```

Create the mapper file:

```bash
cat > mr-test/mapper.py <<'PY'
import argparse, json

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        for word in line.split():
            out.write(json.dumps([word, 1]) + "\n")
PY
```

Create the reducer file:

```bash
cat > mr-test/reducer.py <<'PY'
import argparse, json

parser = argparse.ArgumentParser()
parser.add_argument("--input")
parser.add_argument("--output")
parser.add_argument("--task-type")
args = parser.parse_args()

with open(args.input) as f, open(args.output, "w") as out:
    for line in f:
        key, values = json.loads(line)
        out.write(f"{key} {sum(values)}\n")
PY
```

### 2. Log in

The default admin user is:

```text
username: admin
password: admin123
```

Log in through the UI service:

```bash
UI_SERVICE_URL=http://$(minikube ip):30080 python3 cli.py login --username admin --password admin123
```

### 3. Submit the MapReduce job

Submit the test job:

```bash
python3 cli.py jobs submit \
  --input mr-test/input.txt \
  --mapper mr-test/mapper.py \
  --reducer mr-test/reducer.py
```

### 4. Check job status

Use the returned job id:

```bash
python3 cli.py jobs view --job-id <job-id>
```

Wait until the job status becomes:

```text
completed
```

### 5. Retrieve the job result

After the job is completed, retrieve the result:

```bash
python3 cli.py jobs retrieve result --job-id <job-id>
```

Expected result:

```text
hello 2
world 1
mapreduce 1
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