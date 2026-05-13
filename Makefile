SHELL := /bin/bash

NS := distributed-systems-project2026
TAG ?= latest

AUTH_IMAGE := ds-project/auth:$(TAG)
MANAGER_IMAGE := ds-project/manager:$(TAG)
UI_IMAGE := ds-project/ui:$(TAG)
WORKER_IMAGE := ds-project/worker:$(TAG)

.PHONY: cluster-ready minikube-start build-images load-images deploy \
	wait-infra seed-admin wait-apps status \
	rebuild-ui rebuild-manager rebuild-worker rebuild-auth \
	redeploy-ui redeploy-manager clean-cluster minikube-delete

cluster-ready: minikube-start build-images load-images deploy status

minikube-start:
	minikube start

build-images:
	docker build -f docker/auth.Dockerfile -t $(AUTH_IMAGE) .
	docker build -f docker/manager.Dockerfile -t $(MANAGER_IMAGE) .
	docker build -f docker/ui.Dockerfile -t $(UI_IMAGE) .
	docker build -f docker/worker.Dockerfile -t $(WORKER_IMAGE) .

load-images:
	minikube image load $(AUTH_IMAGE)
	minikube image load $(MANAGER_IMAGE)
	minikube image load $(UI_IMAGE)
	minikube image load $(WORKER_IMAGE)

deploy:
	kubectl apply -f k8s/namespace.yaml
	kubectl apply -f k8s/auth.yaml
	kubectl apply -f k8s/dds.yaml
	kubectl apply -f k8s/minio.yaml
	$(MAKE) wait-infra
	kubectl apply -f k8s/auth-seed-admin.yaml
	$(MAKE) seed-admin
	kubectl apply -f k8s/manager.yaml
	kubectl apply -f k8s/ui.yaml
	$(MAKE) wait-apps

wait-infra:
	kubectl rollout status statefulset/auth-postgres -n $(NS) --timeout=180s
	kubectl rollout status statefulset/dds-postgres -n $(NS) --timeout=180s
	kubectl rollout status statefulset/minio -n $(NS) --timeout=180s
	kubectl rollout status deployment/auth -n $(NS) --timeout=180s

seed-admin:
	kubectl wait --for=condition=complete job/auth-seed-admin -n $(NS) --timeout=180s

wait-apps:
	kubectl rollout status statefulset/manager -n $(NS) --timeout=240s
	kubectl rollout status deployment/ui -n $(NS) --timeout=180s

status:
	kubectl get pods -n $(NS)
	kubectl get svc -n $(NS)
	@echo
	@echo "CLI URL for Minikube NodePort:"
	@echo "  UI_SERVICE_URL=http://$$(minikube ip):30080 python3 cli.py login --username admin --password admin123"

rebuild-ui:
	docker build -f docker/ui.Dockerfile -t $(UI_IMAGE) .
	minikube image load $(UI_IMAGE)

rebuild-manager:
	docker build -f docker/manager.Dockerfile -t $(MANAGER_IMAGE) .
	minikube image load $(MANAGER_IMAGE)

rebuild-worker:
	docker build -f docker/worker.Dockerfile -t $(WORKER_IMAGE) .
	minikube image load $(WORKER_IMAGE)

rebuild-auth:
	docker build -f docker/auth.Dockerfile -t $(AUTH_IMAGE) .
	minikube image load $(AUTH_IMAGE)

redeploy-ui: rebuild-ui
	kubectl rollout restart deployment/ui -n $(NS)
	kubectl rollout status deployment/ui -n $(NS) --timeout=180s

redeploy-manager: rebuild-manager
	kubectl rollout restart statefulset/manager -n $(NS)
	kubectl rollout status statefulset/manager -n $(NS) --timeout=240s

clean-cluster:
	kubectl delete namespace $(NS) --ignore-not-found=true

minikube-delete:
	minikube delete
