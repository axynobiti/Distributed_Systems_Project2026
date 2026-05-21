#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-distributed-systems-project2026}"
KILL_COUNT="${KILL_COUNT:-3}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
DEMO_SLEEP_SECONDS="${DEMO_SLEEP_SECONDS:-45}"
WATCH_SECONDS="${WATCH_SECONDS:-45}"

if [ -z "${UI_SERVICE_URL:-}" ]; then
  UI_SERVICE_URL="http://$(minikube ip):30080"
fi

RED="\033[31m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
RESET="\033[0m"

WORK_DIR="$(mktemp -d)"
TOKEN=""
DELETED_PODS=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_INPUT="${TEST_INPUT:-$SCRIPT_DIR/WordCount/wordcount_input.txt}"
TEST_MAPPER="${TEST_MAPPER:-$SCRIPT_DIR/WordCount/wordcount_mapper.py}"
TEST_REDUCER="${TEST_REDUCER:-$SCRIPT_DIR/WordCount/wordcount_reducer.py}"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

title() {
  echo ""
  echo -e "${BLUE}==============================${RESET}"
  echo -e "${BLUE} $1${RESET}"
  echo -e "${BLUE}==============================${RESET}"
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo -e "${RED}Missing required command: $1${RESET}"
    exit 1
  fi
}

check_dependencies() {
  need_command kubectl
  need_command curl
  need_command python3

  for file in "$TEST_INPUT" "$TEST_MAPPER" "$TEST_REDUCER"; do
    if [ ! -f "$file" ]; then
      echo -e "${RED}Missing test file: $file${RESET}"
      exit 1
    fi
  done
}

login() {
  echo -e "${YELLOW}Logging in through UI service: $UI_SERVICE_URL${RESET}"

  local response
  response="$(curl -fsS \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USERNAME\",\"password\":\"$ADMIN_PASSWORD\"}" \
    "$UI_SERVICE_URL/login")"

  TOKEN="$(python3 -c '
import json, sys
data = json.load(sys.stdin)
token = data.get("access_token")
if not token:
    raise SystemExit(f"Login did not return an access token: {data}")
print(token)
' <<< "$response")"
}

create_demo_files() {
  local target_bytes="$1"

  head -c "$target_bytes" "$TEST_INPUT" > "$WORK_DIR/input.txt"

  {
    printf 'import time\n'
    printf 'time.sleep(%s)\n\n' "$DEMO_SLEEP_SECONDS"
    sed '/^import time$/d' "$TEST_MAPPER"
  } > "$WORK_DIR/slow_mapper.py"

  {
    printf 'import time\n'
    printf 'time.sleep(%s)\n\n' "$DEMO_SLEEP_SECONDS"
    sed '/^import time$/d' "$TEST_REDUCER"
  } > "$WORK_DIR/slow_reducer.py"
}

submit_demo_job() {
  local target_bytes="$1"
  create_demo_files "$target_bytes"

  echo -e "${YELLOW}Submitting slow demo job using test-files/WordCount...${RESET}" >&2
  echo "Input source: $TEST_INPUT" >&2
  echo "Mapper source: $TEST_MAPPER" >&2
  echo "Reducer source: $TEST_REDUCER" >&2

  local response
  response="$(curl -fsS \
    -H "Authorization: Bearer $TOKEN" \
    -F "input_file=@$WORK_DIR/input.txt" \
    -F "mapper_file=@$WORK_DIR/slow_mapper.py" \
    -F "reducer_file=@$WORK_DIR/slow_reducer.py" \
    "$UI_SERVICE_URL/jobs")"

  python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data.get("success"):
    raise SystemExit(f"Job submission failed: {data}")
print(data["job_id"])
' <<< "$response"
}

job_json() {
  local job_id="$1"
  curl -fsS \
    -H "Authorization: Bearer $TOKEN" \
    "$UI_SERVICE_URL/jobs/$job_id"
}

try_job_json() {
  local job_id="$1"
  curl -sS \
    -H "Authorization: Bearer $TOKEN" \
    "$UI_SERVICE_URL/jobs/$job_id"
}

job_field() {
  local job_id="$1"
  local field="$2"

  try_job_json "$job_id" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print('')
    raise SystemExit(0)
if not isinstance(data, dict) or 'job_id' not in data:
    print('')
    raise SystemExit(0)
value = data.get('$field')
print('' if value is None else value)
"
}

print_job_summary() {
  local job_id="$1"

  try_job_json "$job_id" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("Job status temporarily unavailable while services recover.")
    raise SystemExit(0)
if not isinstance(data, dict) or "job_id" not in data:
    detail = data.get("detail") if isinstance(data, dict) else data
    print(f"Job status temporarily unavailable: {detail}")
    raise SystemExit(0)
progress = data.get("task_progress", {})
print(
    "Job {} | manager: {} | status: {} | tasks: {}/{} completed, "
    "{} running, {} pending, {} failed".format(
        data.get("job_id"),
        data.get("manager_id"),
        data.get("status"),
        progress.get("completed", 0),
        progress.get("total", 0),
        progress.get("running", 0),
        progress.get("pending", 0),
        progress.get("failed", 0),
    )
)
for task in data.get("tasks", []):
    print(
        "  - {} {:02d} | status={} | attempts={} | k8s_job={}".format(
            task["task_type"],
            task["task_index"],
            task["status"],
            task["attempt_count"],
            task.get("kubernetes_job_name"),
        )
    )
'
}

worker_selector() {
  local job_id="$1"
  local task_type="${2:-}"

  if [ -n "$task_type" ]; then
    echo "app=mapreduce-worker,mapreduce-job-id=$job_id,mapreduce-task-type=$task_type"
  else
    echo "app=mapreduce-worker,mapreduce-job-id=$job_id"
  fi
}

running_worker_pods() {
  local job_id="$1"
  local task_type="${2:-}"

  kubectl get pods -n "$NAMESPACE" \
    -l "$(worker_selector "$job_id" "$task_type")" \
    --no-headers 2>/dev/null \
    | awk '$3 == "Running" {print $1}'
}

wait_for_running_pods() {
  local job_id="$1"
  local task_type="$2"
  local expected_count="$3"
  local timeout_seconds="$4"

  echo -e "${YELLOW}Waiting for at least $expected_count running $task_type pod(s)...${RESET}"

  for _ in $(seq 1 "$timeout_seconds"); do
    local count
    count="$(running_worker_pods "$job_id" "$task_type" | wc -l)"

    if [ "$count" -ge "$expected_count" ]; then
      kubectl get pods -n "$NAMESPACE" -l "$(worker_selector "$job_id" "$task_type")"
      return 0
    fi

    sleep 1
  done

  echo -e "${RED}Timed out waiting for running $task_type pods.${RESET}"
  kubectl get pods -n "$NAMESPACE" -l "$(worker_selector "$job_id" "$task_type")" || true
  print_job_summary "$job_id" || true
  exit 1
}

wait_for_phase_to_exist() {
  local job_id="$1"
  local task_type="$2"
  local timeout_seconds="$3"

  echo -e "${YELLOW}Waiting for $task_type phase to be created...${RESET}"

  for _ in $(seq 1 "$timeout_seconds"); do
    if kubectl get jobs -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id" "$task_type")" \
      --no-headers 2>/dev/null | grep -q .; then
      return 0
    fi

    sleep 1
  done

  echo -e "${RED}Timed out waiting for $task_type Kubernetes Jobs.${RESET}"
  print_job_summary "$job_id" || true
  exit 1
}

watch_job() {
  local job_id="$1"
  local seconds="${2:-$WATCH_SECONDS}"

  for i in $(seq 1 "$seconds"); do
    echo ""
    echo "----- second $i/$seconds -----"
    print_job_summary "$job_id" || true

    echo ""
    echo "Worker pods:"
    kubectl get pods -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id")" \
      --sort-by=.metadata.creationTimestamp || true

    echo ""
    echo "Worker Kubernetes Jobs:"
    kubectl get jobs -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id")" \
      --sort-by=.metadata.creationTimestamp || true

    local status
    status="$(job_field "$job_id" "status" || true)"
    if [ "$status" = "completed" ] || [ "$status" = "failed" ]; then
      return 0
    fi

    sleep 1
  done
}

job_task_progress_field() {
  local job_id="$1"
  local field="$2"

  try_job_json "$job_id" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print('0')
    raise SystemExit(0)
progress = data.get('task_progress') if isinstance(data, dict) else {}
print(progress.get('$field', 0) if isinstance(progress, dict) else 0)
"
}

watch_managers_and_job() {
  local job_id="$1"
  local expected_manager="$2"
  local seconds="$3"

  for i in $(seq 1 "$seconds"); do
    echo ""
    echo "----- second $i/$seconds -----"
    echo "Manager pods:"
    kubectl get pods -n "$NAMESPACE" -l app=manager || true

    echo ""
    print_job_summary "$job_id" || true

    local current_manager
    current_manager="$(job_field "$job_id" "manager_id" || true)"
    if [ "$current_manager" = "$expected_manager" ]; then
      echo -e "${GREEN}DDS still assigns the job to the same Manager identity: $current_manager${RESET}"
    fi

    local manager_ready
    local running_tasks
    manager_ready="$(kubectl get pod "$expected_manager" -n "$NAMESPACE" \
      -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || true)"
    running_tasks="$(job_task_progress_field "$job_id" "running")"

    if [ "$manager_ready" = "true" ] && [ "$current_manager" = "$expected_manager" ] && [ "$running_tasks" -gt 0 ]; then
      echo -e "${GREEN}Manager is back and the job is running again. Case 1 proof complete.${RESET}"
      return 0
    fi

    sleep 1
  done
}

delete_running_worker_pods() {
  local job_id="$1"
  local task_type="$2"
  local kill_count="$3"

  local pods
  pods="$(running_worker_pods "$job_id" "$task_type" | head -n "$kill_count" || true)"

  if [ -z "$pods" ]; then
    echo -e "${RED}No running $task_type pods found for job $job_id.${RESET}"
    exit 1
  fi

  echo -e "${RED}Deleting these running $task_type pod(s):${RESET}"
  echo "$pods"
  DELETED_PODS="$pods"

  for pod in $pods; do
    kubectl delete pod "$pod" -n "$NAMESPACE" --wait=false
  done
}

max_task_attempt_for_phase() {
  local job_id="$1"
  local task_type="$2"

  try_job_json "$job_id" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print(0)
    raise SystemExit(0)
attempts = [
    task.get('attempt_count', 0)
    for task in data.get('tasks', [])
    if task.get('task_type') == '$task_type'
]
print(max(attempts) if attempts else 0)
"
}

watch_task_recovery() {
  local job_id="$1"
  local task_type="$2"
  local seconds="$3"

  for i in $(seq 1 "$seconds"); do
    echo ""
    echo "----- recovery second $i/$seconds -----"
    print_job_summary "$job_id" || true

    echo ""
    echo "Worker pods for $task_type phase:"
    kubectl get pods -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id" "$task_type")" \
      --sort-by=.metadata.creationTimestamp || true

    echo ""
    echo "Worker Kubernetes Jobs for $task_type phase:"
    kubectl get jobs -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id" "$task_type")" \
      --sort-by=.metadata.creationTimestamp || true

    local retry_pod
    retry_pod="$(kubectl get pods -n "$NAMESPACE" \
      -l "$(worker_selector "$job_id" "$task_type")" \
      --no-headers 2>/dev/null \
      | awk '$1 ~ /attempt-00[2-9]/ && ($3 == "Running" || $3 == "Completed") {print $1; exit}')"

    if [ -n "$retry_pod" ]; then
      echo -e "${GREEN}Retry worker pod is visible: $retry_pod${RESET}"
      echo -e "${GREEN}Case 2 proof complete: the failed task execution was rescheduled.${RESET}"
      return 0
    fi

    local max_attempt
    max_attempt="$(max_task_attempt_for_phase "$job_id" "$task_type")"
    if [ "$max_attempt" -gt 1 ]; then
      echo -e "${GREEN}A retry attempt is visible for $task_type tasks. Case 2 proof complete.${RESET}"
      return 0
    fi

    local status
    status="$(job_field "$job_id" "status" || true)"
    if [ "$status" = "completed" ]; then
      echo -e "${GREEN}Job completed after the pod kill. Case 2 proof complete.${RESET}"
      return 0
    fi

    sleep 1
  done

  echo -e "${YELLOW}Recovery watch ended. The job may still be reconciling; check option 4 or rerun kubectl get pods/jobs.${RESET}"
}

case_manager_restart_same_owner() {
  title "Case 1: Manager pod dies while two tasks run"
  echo "This submits a job that creates two map tasks, kills the Manager pod that owns the job, and shows the StatefulSet recreating the same Manager identity."

  local job_id
  job_id="$(submit_demo_job 5600000)"
  echo -e "${GREEN}Submitted demo job: $job_id${RESET}"

  wait_for_running_pods "$job_id" "map" 2 90

  local manager_id
  manager_id="$(job_field "$job_id" "manager_id")"

  echo ""
  echo -e "${YELLOW}Job $job_id is owned by Manager identity: $manager_id${RESET}"
  print_job_summary "$job_id"

  echo ""
  echo -e "${RED}Deleting Manager pod $manager_id...${RESET}"
  kubectl delete pod "$manager_id" -n "$NAMESPACE" --wait=false

  watch_managers_and_job "$job_id" "$manager_id" 25

  echo ""
  echo -e "${GREEN}Final state:${RESET}"
  print_job_summary "$job_id"
}

case_kill_task_pods() {
  title "Case 2: Kill running map/reduce pods"
  echo "This submits a larger slow job, deletes several running worker pods, and shows Kubernetes/Manager recovery until the job completes."

  echo ""
  read -r -p "Kill map pods or reduce pods? [map/reduce] " phase
  if [ "$phase" != "map" ] && [ "$phase" != "reduce" ]; then
    echo -e "${RED}Please choose either map or reduce.${RESET}"
    exit 1
  fi

  local job_id
  job_id="$(submit_demo_job 17000000)"
  echo -e "${GREEN}Submitted demo job: $job_id${RESET}"

  if [ "$phase" = "reduce" ]; then
    wait_for_phase_to_exist "$job_id" "reduce" 180
  fi

  wait_for_running_pods "$job_id" "$phase" 1 180
  delete_running_worker_pods "$job_id" "$phase" "$KILL_COUNT"

  echo ""
  echo -e "${YELLOW}Watching recovery. The script exits as soon as it sees a replacement pod or retry attempt.${RESET}"
  watch_task_recovery "$job_id" "$phase" "$WATCH_SECONDS"
}

show_state() {
  title "Current Cluster State"
  echo "Manager pods:"
  kubectl get pods -n "$NAMESPACE" -l app=manager || true

  local latest_job_id
  latest_job_id="$(kubectl get jobs -n "$NAMESPACE" \
    -l app=mapreduce-worker \
    --sort-by=.metadata.creationTimestamp \
    -o jsonpath='{range .items[*]}{.metadata.labels.mapreduce-job-id}{"\n"}{end}' 2>/dev/null \
    | awk 'NF {latest=$1} END {print latest}')"

  if [ -z "$latest_job_id" ]; then
    echo ""
    echo "No MapReduce worker pods/jobs found yet."
    return 0
  fi

  echo ""
  echo "Latest MapReduce job id: $latest_job_id"

  echo ""
  echo "Worker pods for latest job:"
  kubectl get pods -n "$NAMESPACE" \
    -l "$(worker_selector "$latest_job_id")" \
    --sort-by=.metadata.creationTimestamp || true

  echo ""
  echo "Worker Kubernetes Jobs for latest job:"
  kubectl get jobs -n "$NAMESPACE" \
    -l "$(worker_selector "$latest_job_id")" \
    --sort-by=.metadata.creationTimestamp || true
}

main() {
  check_dependencies
  login

  title "Fault Tolerance Live Demo"
  echo "1) Manager dies while two tasks are running; same Manager identity resumes"
  echo "2) Kill several running map/reduce pods; task eventually completes"
  echo "3) Show current pods/jobs"
  echo ""

  read -r -p "Choose demo case: " selected_case

  case "$selected_case" in
    1) case_manager_restart_same_owner ;;
    2) case_kill_task_pods ;;
    3) show_state ;;
    *)
      echo -e "${RED}Invalid choice.${RESET}"
      exit 1
      ;;
  esac
}

main "$@"
