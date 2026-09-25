# shellcheck shell=sh
# Shared helpers for the host scripts: load the one settings file (C-21). POSIX sh.
VC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VC_SETTINGS=${VC_SETTINGS:-$VC_ROOT/settings.env}

vc_die() { echo "vc: $*" >&2; exit 1; }

vc_abs() {  # resolve ~ and repo-relative paths
  case $1 in
    "~"|"~/"*) printf '%s\n' "$HOME${1#\~}" ;;
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$VC_ROOT/$1" ;;
  esac
}

vc_load() {
  [ -f "$VC_SETTINGS" ] || vc_die "settings file missing: $VC_SETTINGS (run: bin/vc-env init)"
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in ''|'#'*) continue ;; esac
    key=${line%%=*}; val=${line#*=}
    case $key in VC_[A-Z0-9_]*) ;; *) vc_die "bad settings line: $key" ;; esac
    eval "$key=\$val"; export "$key"
  done < "$VC_SETTINGS"
  VC_RUNS_ABS=$(vc_abs "$VC_RUNS_DIR"); VC_STATE_ABS=$(vc_abs "$VC_STATE_DIR")
  VC_ENV_FILE_ABS=$(vc_abs "$VC_ENV_FILE"); VC_REPO_ABS=$VC_ROOT
  export VC_RUNS_ABS VC_STATE_ABS VC_ENV_FILE_ABS VC_REPO_ABS
}

vc_compose_env() {  # resolved env file for docker compose (paths absolute); no secrets in it
  mkdir -p "$VC_STATE_ABS"
  f=$VC_STATE_ABS/compose.env
  { grep -E '^VC_[A-Z0-9_]+=' "$VC_SETTINGS" | grep -vE '^VC_(RUNS_DIR|STATE_DIR|ENV_FILE)='
    echo "VC_RUNS_ABS=$VC_RUNS_ABS"; echo "VC_STATE_ABS=$VC_STATE_ABS"
    echo "VC_ENV_FILE_ABS=$VC_ENV_FILE_ABS"; echo "VC_REPO_ABS=$VC_REPO_ABS"; } > "$f"
  printf '%s\n' "$f"
}

vc_compose() {
  docker compose --env-file "$(vc_compose_env)" -f "$VC_ROOT/env/compose.yaml" "$@"
}

vc_host_dirs() {  # for host tools (vc-loop, vc-spec): runs/state dirs from the settings file, unless set already
  _runs=${VC_RUNS_DIR:-}; _state=${VC_STATE_DIR:-}
  if [ -f "$VC_SETTINGS" ]; then
    vc_load
    VC_RUNS_DIR=${_runs:-$VC_RUNS_ABS}; VC_STATE_DIR=${_state:-$VC_STATE_ABS}
  else
    VC_RUNS_DIR=${_runs:-$VC_ROOT/runs}; VC_STATE_DIR=${_state:-$VC_ROOT/state}
  fi
  export VC_RUNS_DIR VC_STATE_DIR
}
