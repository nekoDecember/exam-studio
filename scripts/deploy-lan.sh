#!/bin/zsh
# Reuse the current project's Compose files so public/monitoring connections survive.
set -euo pipefail
task_root="${0:A:h:h}"
cd "$task_root"
typeset -a task_compose_files
typeset -a task_compose_command
if docker compose version >/dev/null 2>&1; then
  task_compose_command=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  task_compose_command=(docker-compose)
else
  print -u2 "Docker Compose is required."
  exit 1
fi
task_public_url="$(docker exec exam-studio-app-1 python -c 'import os; print(os.getenv("PUBLIC_URL", ""))' 2>/dev/null || true)"
if [[ -n "$task_public_url" ]]; then
  export PUBLIC_URL="$task_public_url"
fi
task_config_files="$(docker inspect exam-studio-app-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)"
if [[ -n "$task_config_files" ]]; then
  for task_file in ${(s:,:)task_config_files}; do
    [[ "$task_file" == "$task_root/compose.lan.yaml" ]] && continue
    [[ -f "$task_file" ]] || { print -u2 "Compose file not found: $task_file"; exit 1; }
    task_compose_files+=(-f "$task_file")
  done
else
  task_compose_files=(-f "$task_root/compose.yaml")
fi
task_compose_files+=(-f "$task_root/compose.lan.yaml")
"${task_compose_command[@]}" --project-name exam-studio --env-file "$task_root/.env" "${task_compose_files[@]}" up -d --build app
task_interface="$(route -n get default | awk '/interface:/{print $2}')"
task_lan_ip="$(ipconfig getifaddr "$task_interface")"
task_url="http://${task_lan_ip}:${LAN_PORT:-8080}"
for task_attempt in {1..30}; do
  if curl --fail --silent "$task_url/api/health" >/dev/null; then
    curl --fail --silent "$task_url/" >/dev/null
    print "Exam is available at $task_url"
    "${task_compose_command[@]}" --project-name exam-studio --env-file "$task_root/.env" "${task_compose_files[@]}" ps app
    exit 0
  fi
  sleep 2
done
"${task_compose_command[@]}" --project-name exam-studio --env-file "$task_root/.env" "${task_compose_files[@]}" logs --tail 30 app
exit 1
