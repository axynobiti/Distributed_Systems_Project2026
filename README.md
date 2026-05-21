# Distributed Systems Project 2026

## Prerequisites

This project assumes Docker, Minikube, and kubectl are already installed and
configured on the machine.

## Start the Kubernetes cluster

Given that Docker and Minikube are installed, start the cluster with:

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

If `cli.py` already points to the correct UI URL, use these commands.

Log in as an existing user:

```bash
python3 cli.py login --username admin --password admin123
```

Check that the saved login token is still valid:

```bash
python3 cli.py validate-token
```

Log out and remove the saved token:

```bash
python3 cli.py logout
```

Given that you are an admin, list all users:

```bash
python3 cli.py admin list-users
```

Given that you are an admin, create a new user:

```bash
python3 cli.py admin create-user \
  --username alice \
  --email alice@example.com \
  --role user
```

Given that you are an admin, create another admin user:

```bash
python3 cli.py admin create-user \
  --username manager \
  --email manager@example.com \
  --role admin
```

Given that you are an admin, delete a user:

```bash
python3 cli.py admin delete-user --username alice
```

List the MapReduce jobs visible to the current user:

```bash
python3 cli.py jobs list
```

Submit a MapReduce job:

```bash
python3 cli.py jobs submit \
  --input <input-file> \
  --mapper <mapper-file> \
  --reducer <reducer-file>
```

View the status and task details of a job:

```bash
python3 cli.py jobs view --job-id <job-id>
```

Retrieve the result of a completed job:

```bash
python3 cli.py jobs retrieve result --job-id <job-id>
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
