#!/usr/bin/env bash

set -Eeuo pipefail

fail() {
  printf 'reproduction failed: %s\n' "$1" >&2
  exit 1
}

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  fail "OPENAI_API_KEY is absent"
fi
if [[ -z "${DYNAMICAL_PROVIDER_RUNTIME_ROOT:-}" ]]; then
  fail "DYNAMICAL_PROVIDER_RUNTIME_ROOT is absent"
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null) ||
  fail "repository root cannot be resolved"
[[ "$SCRIPT_DIR" == "$REPO_ROOT/scripts" ]] || fail "script is outside the repository"

for dependency in uv jq ffmpeg ffprobe git nvidia-smi sha256sum docker; do
  command -v "$dependency" >/dev/null 2>&1 || fail "required command is absent: $dependency"
done
nvidia-smi -L >/dev/null 2>&1 || fail "an available NVIDIA GPU is required"
docker info >/dev/null 2>&1 || fail "the Docker daemon is unavailable"

PROVIDER_ROOT=$(CDPATH= cd -- "$DYNAMICAL_PROVIDER_RUNTIME_ROOT" 2>/dev/null && pwd -P) ||
  fail "provider runtime root is absent"
PROVIDER_PYTHON="$PROVIDER_ROOT/source/.venv-matterix/bin/python"
MATTERIX_ROOT="$PROVIDER_ROOT/source/external/Matterix"
ISAACLAB_ROOT="$PROVIDER_ROOT/IsaacLab-v2.3.0-src"
[[ -x "$PROVIDER_PYTHON" ]] || fail "MATTERIX Python is absent"
[[ -d "$MATTERIX_ROOT" ]] || fail "MATTERIX source is absent"
[[ -d "$ISAACLAB_ROOT" ]] || fail "Isaac Lab source is absent"
[[ -f /lib/aarch64-linux-gnu/libgomp.so.1 ]] || fail "required ARM64 libgomp is absent"

FONT_FILE=
for candidate in \
  /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf \
  /System/Library/Fonts/Menlo.ttc; do
  if [[ -f "$candidate" ]]; then
    FONT_FILE=$candidate
    break
  fi
done
[[ -n "$FONT_FILE" ]] || fail "a supported monospace font is absent"

RUN_STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
RUN_ID=$(printf '%s' "$RUN_STAMP" | tr '[:upper:]' '[:lower:]')
RUN_ROOT="$REPO_ROOT/artifacts/v0.1/$RUN_STAMP"
mkdir -m 700 -p "$RUN_ROOT" || fail "run directory creation failed"
WORKSPACE="$RUN_ROOT/workspace"
mkdir -p "$WORKSPACE"
cp "$REPO_ROOT/examples/luna-demo/WORKSPACE.md" "$WORKSPACE/WORKSPACE.md"

LUNA_PID=
MATTERIX_PID=
LUNA_CONTAINER="dynamical-luna-$RUN_ID"
cleanup() {
  local status=$?
  trap - EXIT INT TERM
  for pid in "$MATTERIX_PID" "$LUNA_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  if docker container inspect "$LUNA_CONTAINER" >/dev/null 2>&1; then
    docker container stop --time 10 "$LUNA_CONTAINER" >/dev/null 2>&1 || true
    docker container rm "$LUNA_CONTAINER" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$REPO_ROOT"
DYNAMICAL=(uv run dynamical)
WHEEL_DIR="$RUN_ROOT/wheel"
mkdir "$WHEEL_DIR"
uv build --wheel --out-dir "$WHEEL_DIR" >"$RUN_ROOT/wheel-build.log" 2>&1 ||
  fail "Dynamical wheel build failed"
[[ -f "$WHEEL_DIR/dynamical-0.1.0-py3-none-any.whl" ]] ||
  fail "expected Dynamical v0.1 wheel is absent"
LUNA_IMAGE="dynamical-luna-demo:v01-$RUN_ID"
docker build \
  -f examples/luna-demo/Dockerfile \
  --build-context "wheel=$WHEEL_DIR" \
  --build-context "demo=$REPO_ROOT/examples/luna-demo" \
  -t "$LUNA_IMAGE" "$REPO_ROOT" >"$RUN_ROOT/luna-image-build.log" 2>&1 ||
  fail "Luna demo image build failed"

LUNA_LOG="$RUN_ROOT/luna-api.log"
docker run --rm --name "$LUNA_CONTAINER" \
  --read-only \
  --user "$(id -u):$(id -g)" \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --pids-limit 512 \
  --mount "type=bind,src=$WORKSPACE,dst=/workspace" \
  --env OPENAI_API_KEY \
  "$LUNA_IMAGE" \
  >"$LUNA_LOG" 2>&1 &
LUNA_PID=$!
while kill -0 "$LUNA_PID" 2>/dev/null; do
  sleep 10
  tail -n 3 "$LUNA_LOG" >&2 || true
done
if ! wait "$LUNA_PID"; then
  LUNA_PID=
  fail "Luna demo did not complete"
fi
LUNA_PID=

DECISION="$WORKSPACE/decision.json"
[[ -f "$DECISION" ]] || fail "Luna did not write decision.json"
DECISION_VALIDATION="$RUN_ROOT/decision-validation.json"
"${DYNAMICAL[@]}" validate "$DECISION" --json >"$DECISION_VALIDATION" ||
  fail "decision.json does not match the Dynamical decision contract"

resolve_workspace_file() {
  uv run python -c '
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
path = (root / sys.argv[2]).resolve(strict=True)
path.relative_to(root)
if not path.is_file():
    raise SystemExit(1)
print(path)
' "$WORKSPACE" "$1"
}

SELECTED_REQUIREMENT=$(resolve_workspace_file "$(jq -er .selected_virtual_campaign "$DECISION")") ||
  fail "selected virtual campaign escapes the workspace or is absent"
PHYSICAL_REQUIREMENT=$(resolve_workspace_file "$(jq -er .physical_route_requirement "$DECISION")") ||
  fail "physical route requirement escapes the workspace or is absent"

VIRTUAL_COMPOSITION="$RUN_ROOT/selected-virtual-composition.json"
VIRTUAL_COMPOSE_RECEIPT="$RUN_ROOT/selected-virtual-compose-receipt.json"
"${DYNAMICAL[@]}" compose "$SELECTED_REQUIREMENT" -o "$VIRTUAL_COMPOSITION" \
  >"$VIRTUAL_COMPOSE_RECEIPT" || fail "selected virtual campaign did not compose"
jq -e '.status == "COMPILED"' "$VIRTUAL_COMPOSE_RECEIPT" >/dev/null ||
  fail "selected virtual campaign is not COMPILED"

PHYSICAL_COMPOSITION="$RUN_ROOT/physical-route-composition.json"
PHYSICAL_COMPOSE_RECEIPT="$RUN_ROOT/physical-route-compose-receipt.json"
set +e
"${DYNAMICAL[@]}" compose "$PHYSICAL_REQUIREMENT" -o "$PHYSICAL_COMPOSITION" \
  >"$PHYSICAL_COMPOSE_RECEIPT"
physical_status=$?
set -e
[[ $physical_status -eq 1 ]] || fail "physical route did not return the required HOLD status"
jq -e '.status == "HOLD"' "$PHYSICAL_COMPOSE_RECEIPT" >/dev/null ||
  fail "physical route receipt is not HOLD"
jq -e '
  .reason_codes as $codes |
  ($codes | index("PROVIDER_NOT_ADMITTED") != null) and
  ($codes | index("PROVIDER_UNAVAILABLE") != null) and
  ($codes | index("APPROVAL_REQUIRED") != null)
' "$PHYSICAL_COMPOSE_RECEIPT" >/dev/null ||
  fail "physical route HOLD is not an authority and availability block"

VIRTUAL_WORLD="$RUN_ROOT/selected-virtual-world"
VIRTUAL_COMPILE_RECEIPT="$RUN_ROOT/selected-virtual-compile-receipt.json"
"${DYNAMICAL[@]}" compile "$VIRTUAL_COMPOSITION" -o "$VIRTUAL_WORLD" \
  >"$VIRTUAL_COMPILE_RECEIPT" || fail "selected virtual campaign did not compile"
jq -e '.status == "passed" and .target == "matterix"' "$VIRTUAL_COMPILE_RECEIPT" \
  >/dev/null || fail "selected virtual compile receipt did not pass"

VIRTUAL_TRACE="$RUN_ROOT/selected-virtual-trace.ndjson"
VIRTUAL_RUN_RECEIPT="$RUN_ROOT/selected-virtual-run-receipt.json"
"${DYNAMICAL[@]}" run "$VIRTUAL_WORLD" -o "$VIRTUAL_TRACE" \
  >"$VIRTUAL_RUN_RECEIPT" || fail "selected virtual campaign did not run"
jq -e '.valid == true' "$VIRTUAL_RUN_RECEIPT" >/dev/null ||
  fail "selected virtual campaign receipt is invalid"

MATTERIX_REQUIREMENT="$RUN_ROOT/selected-matterix-branch.json"
jq -e '
  ([.steps[] | select(.operation_id == "apply-thermal-program")] | length) == 1
' "$PHYSICAL_REQUIREMENT" >/dev/null ||
  fail "selected physical route does not define one MATTERIX thermal branch"
jq '
  (.steps[] | select(.operation_id == "apply-thermal-program")) as $thermal |
  ($thermal.input_bindings[] |
    select(.target_port_id == "instrument.agitation_rate_rpm")) as $agitation_binding |
  (if $agitation_binding.source_kind == "campaign_input" then
    (.inputs[] | select(.id == $agitation_binding.source_id) |
      {unit: .unit, value: .value})
  else
    (.steps[] | select(.step_id == $agitation_binding.source_id) |
      .parameters[] | select(.name == "agitation-rate") |
      {unit: .unit, value: .value})
  end) as $agitation |
  {
    document_type: "dynamical.campaign-requirement",
    schema_version: "0.1.0",
    requirement_id: "selected-matterix-embodied-branch",
    objective: {
      id: "execute-selected-matterix-embodied-branch",
      statement: "Execute the selected thermal branch through the admitted MATTERIX provider task.",
      decision: "Record the admitted provider task execution without treating it as physical proof.",
      proof_requirements: [{
        id: "selected-thermal-branch-execution",
        operation_id: "apply-thermal-program",
        output_port_ids: ["thermal.sample_temperature_K"],
        minimum_evidence_class: "simulator",
        acceptance_rule: "The MATTERIX provider trace and receipt validate independently.",
        independent_verification_required: true
      }]
    },
    inputs: [
      (.inputs[] | select(.id == "material.mass_kg" or .id == "material.temperature_K")),
      {
        id: "instrument.agitation_rate_rpm",
        state_type: "number",
        unit: $agitation.unit,
        value: $agitation.value
      }
    ],
    steps: [{
      step_id: "selected-thermal-branch",
      operation_id: "apply-thermal-program",
      minimum_evidence_class: "simulator",
      parameters: $thermal.parameters,
      input_bindings: [
        {
          target_port_id: "material.mass_kg",
          source_kind: "campaign_input",
          source_id: "material.mass_kg"
        },
        {
          target_port_id: "material.temperature_K",
          source_kind: "campaign_input",
          source_id: "material.temperature_K"
        },
        {
          target_port_id: "instrument.agitation_rate_rpm",
          source_kind: "campaign_input",
          source_id: "instrument.agitation_rate_rpm"
        }
      ],
      depends_on: [],
      required_policy_tags: ["simulation-only", "scale-transfer-unvalidated"]
    }],
    max_cost_usd: 0.0,
    max_duration_s: ($thermal.parameters[] |
      select(.name == "dwell-time") | .value)
  }
' "$PHYSICAL_REQUIREMENT" >"$MATTERIX_REQUIREMENT" ||
  fail "selected MATTERIX branch projection failed"

MATTERIX_COMPOSITION="$RUN_ROOT/selected-matterix-composition.json"
MATTERIX_COMPOSE_RECEIPT="$RUN_ROOT/selected-matterix-compose-receipt.json"
"${DYNAMICAL[@]}" compose "$MATTERIX_REQUIREMENT" -o "$MATTERIX_COMPOSITION" \
  >"$MATTERIX_COMPOSE_RECEIPT" || fail "selected MATTERIX branch did not compose"
jq -e '.status == "COMPILED"' "$MATTERIX_COMPOSE_RECEIPT" >/dev/null ||
  fail "selected MATTERIX branch is not COMPILED"
jq -e '
  .virtual_sdl.operation_bindings as $bindings |
  ($bindings | length) == 1 and
  $bindings[0].operation_id == "apply-thermal-program" and
  $bindings[0].provider_id == "matterix-heater-workstation-simulator" and
  $bindings[0].evidence_class == "simulator" and
  ([$bindings[0].adapter_links[].adapter_id] |
    index("dynamical-matterix-heater-control") != null) and
  ([$bindings[0].adapter_links[].adapter_id] |
    index("dynamical-matterix-franka-control") != null)
' "$MATTERIX_COMPOSITION" >/dev/null ||
  fail "selected thermal branch did not bind to the admitted MATTERIX provider"

COMPILED_WORLD="$RUN_ROOT/selected-matterix-world"
COMPILE_RECEIPT="$RUN_ROOT/compile-receipt.json"
"${DYNAMICAL[@]}" compile "$MATTERIX_COMPOSITION" -o "$COMPILED_WORLD" \
  >"$COMPILE_RECEIPT" || fail "selected campaign did not compile for MATTERIX"
jq -e '.status == "passed" and .target == "matterix"' "$COMPILE_RECEIPT" >/dev/null ||
  fail "MATTERIX compile receipt did not pass"

RUNTIME_CAMPAIGN="$COMPILED_WORLD/runtime_campaign.json"
BACKEND_CONFIG="$COMPILED_WORLD/backend_config.json"
[[ -f "$RUNTIME_CAMPAIGN" && -f "$BACKEND_CONFIG" ]] ||
  fail "compiled MATTERIX provider artifacts are absent"
jq -e '.execution_status == "requires_external_runtime_gate"' "$RUNTIME_CAMPAIGN" \
  >/dev/null || fail "selected provider is not ready for the external MATTERIX gate"
jq -e '[.actions[].provider_id] | unique | length == 1' \
  "$RUNTIME_CAMPAIGN" >/dev/null || fail "selected MATTERIX provider is absent or ambiguous"

MAX_STEPS=$(jq -r '
  ([.actions[] | select(.kind == "wait") | .parameters.duration] | max // 0) as $wait |
  (.actions | length) as $actions |
  [$wait, (30 * $actions)] | max
' "$RUNTIME_CAMPAIGN" | awk '{ raw=$1*12; steps=int(raw); if (steps<raw) steps++; print steps+1 }')
[[ "$MAX_STEPS" =~ ^[1-9][0-9]*$ ]] || fail "campaign-derived MATTERIX horizon is invalid"

MATTERIX_OUTPUT="$RUN_ROOT/matterix-execution"
MATTERIX_LOG="$MATTERIX_OUTPUT/launcher.log"
mkdir -p "$MATTERIX_OUTPUT"
env \
  MATTERIX_PATH="$MATTERIX_ROOT" \
  OMNI_KIT_ACCEPT_EULA=YES \
  ACCEPT_EULA=Y \
  LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 \
  "$PROVIDER_PYTHON" "$COMPILED_WORLD/run_matterix_gate.py" \
    --compiled-world "$COMPILED_WORLD" \
    --matterix-root "$MATTERIX_ROOT" \
    --output "$MATTERIX_OUTPUT" \
    --max-steps-per-action "$MAX_STEPS" \
    --headless --enable_cameras --device cuda:0 \
    >"$MATTERIX_LOG" 2>&1 &
MATTERIX_PID=$!
while kill -0 "$MATTERIX_PID" 2>/dev/null; do
  sleep 15
  tail -n 5 "$MATTERIX_LOG" >&2 || true
done
if ! wait "$MATTERIX_PID"; then
  MATTERIX_PID=
  fail "real MATTERIX provider task did not pass"
fi
MATTERIX_PID=

MATTERIX_RECEIPT="$MATTERIX_OUTPUT/runtime_evidence.json"
MATTERIX_TRACE="$MATTERIX_OUTPUT/campaign_trace.ndjson"
MATTERIX_VIDEO="$MATTERIX_OUTPUT/matterix-runtime.mp4"
[[ -s "$MATTERIX_RECEIPT" && -s "$MATTERIX_TRACE" && -s "$MATTERIX_VIDEO" ]] ||
  fail "MATTERIX receipt, trace, or video is absent"
jq -e '.receipt_complete == true and .execution_status == "passed" and .intended_exit_code == 0' \
  "$MATTERIX_RECEIPT" >/dev/null || fail "MATTERIX runtime receipt did not pass"

VIRTUAL_WORLD_VALIDATION="$RUN_ROOT/virtual-world-validation.json"
MATTERIX_WORLD_VALIDATION="$RUN_ROOT/matterix-world-validation.json"
VIRTUAL_VALIDATION="$RUN_ROOT/virtual-trace-validation.json"
PHYSICAL_VALIDATION="$RUN_ROOT/physical-route-validation.json"
MATTERIX_VALIDATION="$RUN_ROOT/matterix-trace-validation.json"
REPLAY_TRACE="$RUN_ROOT/matterix-replay.ndjson"
REPLAY_RECEIPT="$RUN_ROOT/matterix-replay-receipt.json"
REPLAY_VALIDATION="$RUN_ROOT/matterix-replay-validation.json"
"${DYNAMICAL[@]}" validate "$VIRTUAL_WORLD" --json >"$VIRTUAL_WORLD_VALIDATION" ||
  fail "selected virtual world validation failed"
"${DYNAMICAL[@]}" validate "$COMPILED_WORLD" --json >"$MATTERIX_WORLD_VALIDATION" ||
  fail "MATTERIX branch world validation failed"
"${DYNAMICAL[@]}" validate "$VIRTUAL_TRACE" --json >"$VIRTUAL_VALIDATION" ||
  fail "virtual-trace validation failed"
"${DYNAMICAL[@]}" validate "$PHYSICAL_COMPOSITION" --json >"$PHYSICAL_VALIDATION" ||
  fail "physical-route validation failed"
"${DYNAMICAL[@]}" validate "$MATTERIX_TRACE" --json >"$MATTERIX_VALIDATION" ||
  fail "MATTERIX-trace validation failed"
"${DYNAMICAL[@]}" run "$MATTERIX_TRACE" --mode replay \
  --compiled-world "$COMPILED_WORLD" --runtime-receipt "$MATTERIX_RECEIPT" \
  -o "$REPLAY_TRACE" >"$REPLAY_RECEIPT" || fail "MATTERIX evidence binding failed"
"${DYNAMICAL[@]}" validate "$REPLAY_TRACE" --json >"$REPLAY_VALIDATION" ||
  fail "MATTERIX replay validation failed"

SCREEN_LOG="$RUN_ROOT/luna-screen.log"
jq -Rrj '
  fromjson? |
  if .type == "response.reasoning_summary_text.delta" then .delta
  elif .type == "response.output_text.delta" then .delta
  elif .type == "workspace.tool" then
    "\n\nTOOL " + .name + " " + (.arguments | tojson) + "\n" +
    (if .result.ok == true then "RESULT OK" else "RESULT ERROR" end) + "\n" +
    ((.result.stdout // "") | tostring) +
    (if (.result.stderr // "") == "" then "" else "\nSTDERR\n" + .result.stderr end) +
    (if .result.error? then "\nERROR\n" + .result.error else "" end) + "\n"
  else ""
  end
' "$LUNA_LOG" | fold -s -w 145 >"$SCREEN_LOG" ||
  fail "Luna screen-log extraction failed"
FINAL_CARD="$RUN_ROOT/final-video-card.txt"
jq -r \
  --arg provider "$(jq -r '[.actions[].provider_id] | unique[0]' "$RUNTIME_CAMPAIGN")" \
  --arg task "$(jq -er .task_id "$BACKEND_CONFIG")" \
  --slurpfile route "$PHYSICAL_COMPOSE_RECEIPT" \
  --slurpfile physical "$PHYSICAL_COMPOSITION" \
  '[
    "SELECTED THERMAL STEP",
    "operation: " + (.selected_physical_experiment.operation | tostring),
    "conditions: " + (.selected_physical_experiment.conditions | tojson),
    "parameters: " + (.selected_physical_experiment.parameters | tojson),
    "measurements: " + (.selected_physical_experiment.measurements | join(", ")),
    "",
    "PHYSICAL ROUTE REQUEST",
    ($physical[0].sources.requirement.steps[] |
      .step_id + ": " + .operation_id +
      (if (.parameters | length) == 0 then ""
       else " " + ([.parameters[] |
         .name + "=" + (.value | tostring) + " " + .unit] | join(", "))
       end)),
    "",
    "UNCERTAINTY",
    (.uncertainty[] | "- " + .),
    "",
    "PROVIDER ROUTE",
    "provider: " + $provider,
    "task: " + $task,
    "physical route: " + $route[0].status,
    "reason codes: " + ($route[0].reason_codes | join(", "))
  ] | .[]' "$DECISION" | fold -s -w 115 >"$FINAL_CARD" ||
  fail "final video card generation failed"
VIDEO_DURATION=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 \
  "$MATTERIX_VIDEO") || fail "MATTERIX video duration cannot be read"
VIDEO_SPEED=$(awk -v duration="$VIDEO_DURATION" 'BEGIN {
  if (duration <= 0) exit 1
  if (duration > 90) print duration / 90
  else print 1
}') || fail "MATTERIX video duration is invalid"

COMBINED_VIDEO="$RUN_ROOT/dynamical-v0.1-side-by-side.mp4"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0x101317:s=960x1080:r=24:d=300" \
  -i "$MATTERIX_VIDEO" \
  -filter_complex \
  "[0:v]drawtext=fontfile='$FONT_FILE':textfile='$SCREEN_LOG':expansion=none:fontcolor=0xD7DEE9:fontsize=16:line_spacing=5:x=18:y=h-(t/270)*(h+text_h),drawtext=fontfile='$FONT_FILE':text='Phase 1 - recorded Luna campaign':fontcolor=white:fontsize=24:box=1:boxcolor=0x101317DD:x=20:y=18[left];[1:v]setpts=PTS/$VIDEO_SPEED,fps=24,scale=960:900:force_original_aspect_ratio=decrease,pad=960:1080:(ow-iw)/2:(oh-ih)/2:0x000000,trim=duration=90,tpad=stop_mode=clone:stop_duration=90,setpts=PTS-STARTPTS,tpad=start_mode=add:start_duration=180:color=black,trim=duration=300[right];[left][right]hstack=inputs=2,drawtext=fontfile='$FONT_FILE':text='Phase 2 starts after Luna selects the embodied branch':fontcolor=white:fontsize=24:box=1:boxcolor=0x000000DD:x=980:y=18:enable='lt(t,180)',drawtext=fontfile='$FONT_FILE':text='Phase 2 - MATTERIX thermal branch projection':fontcolor=white:fontsize=24:box=1:boxcolor=0x000000DD:x=980:y=18:enable='gte(t,180)',drawtext=fontfile='$FONT_FILE':textfile='$FINAL_CARD':expansion=none:fontcolor=white:fontsize=16:line_spacing=5:box=1:boxcolor=0x000000EE:x=60:y=70:enable='gte(t,270)',drawtext=fontfile='$FONT_FILE':text='MATTERIX runs the selected heater setpoint and dwell. It does not execute the full physical route or load the Dynamical OpenUSD stage.':fontcolor=white:fontsize=16:box=1:boxcolor=0x000000DD:x=(w-text_w)/2:y=h-40[out]" \
  -map '[out]' -t 300 -an -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -movflags +faststart "$COMBINED_VIDEO" || fail "side-by-side video generation failed"

FINAL_JSON="$RUN_ROOT/final.json"
jq -cn \
  --arg run_root "$RUN_ROOT" \
  --arg virtual_world "$VIRTUAL_WORLD" \
  --arg compiled_world "$COMPILED_WORLD" \
  --arg virtual_trace "$VIRTUAL_TRACE" \
  --arg matterix_trace "$MATTERIX_TRACE" \
  --arg matterix_receipt "$MATTERIX_RECEIPT" \
  --arg matterix_video "$MATTERIX_VIDEO" \
  --arg combined_video "$COMBINED_VIDEO" \
  --arg luna_log "$LUNA_LOG" \
  --arg provider_id "$(jq -r '[.actions[].provider_id] | unique[0]' "$RUNTIME_CAMPAIGN")" \
  --arg task_id "$(jq -er .task_id "$BACKEND_CONFIG")" \
  --arg workflow "$(jq -er .workflow "$BACKEND_CONFIG")" \
  --argjson max_steps "$MAX_STEPS" \
  --slurpfile decision "$DECISION" \
  --slurpfile decision_validation "$DECISION_VALIDATION" \
  --slurpfile physical_compose "$PHYSICAL_COMPOSE_RECEIPT" \
  --slurpfile virtual_world_validation "$VIRTUAL_WORLD_VALIDATION" \
  --slurpfile matterix_world_validation "$MATTERIX_WORLD_VALIDATION" \
  --slurpfile virtual_validation "$VIRTUAL_VALIDATION" \
  --slurpfile physical_validation "$PHYSICAL_VALIDATION" \
  --slurpfile matterix_validation "$MATTERIX_VALIDATION" \
  --slurpfile replay_validation "$REPLAY_VALIDATION" \
  --arg virtual_trace_sha256 "$(sha256sum "$VIRTUAL_TRACE" | awk '{print $1}')" \
  --arg luna_trace_sha256 "$(sha256sum "$LUNA_LOG" | awk '{print $1}')" \
  --arg decision_sha256 "$(sha256sum "$DECISION" | awk '{print $1}')" \
  --arg matterix_trace_sha256 "$(sha256sum "$MATTERIX_TRACE" | awk '{print $1}')" \
  --arg matterix_receipt_sha256 "$(sha256sum "$MATTERIX_RECEIPT" | awk '{print $1}')" \
  --arg matterix_video_sha256 "$(sha256sum "$MATTERIX_VIDEO" | awk '{print $1}')" \
  --arg combined_video_sha256 "$(sha256sum "$COMBINED_VIDEO" | awk '{print $1}')" \
  '{
    schema_version: "dynamical.reproduction.v0.1",
    status: "passed",
    run_root: $run_root,
    decision: $decision[0],
    decision_sha256: $decision_sha256,
    selected_provider: {
      provider_id: $provider_id,
      task_id: $task_id,
      workflow: $workflow,
      max_steps_per_action: $max_steps
    },
    physical_route: {
      status: $physical_compose[0].status,
      reason_codes: $physical_compose[0].reason_codes
    },
    validation: {
      agent_decision: ($decision_validation[0].valid == true),
      selected_virtual_world: ($virtual_world_validation[0].valid == true),
      matterix_branch_world: ($matterix_world_validation[0].valid == true),
      virtual_trace: ($virtual_validation[0].valid == true),
      physical_route: ($physical_validation[0].valid == true),
      matterix_trace: ($matterix_validation[0].valid == true),
      matterix_replay: ($replay_validation[0].valid == true)
    },
    artifacts: {
      agent_trace: {path: $luna_log, sha256: $luna_trace_sha256},
      selected_virtual_world: $virtual_world,
      matterix_branch_world: $compiled_world,
      virtual_trace: {path: $virtual_trace, sha256: $virtual_trace_sha256},
      matterix_trace: {path: $matterix_trace, sha256: $matterix_trace_sha256},
      matterix_receipt: {path: $matterix_receipt, sha256: $matterix_receipt_sha256},
      matterix_video: {path: $matterix_video, sha256: $matterix_video_sha256},
      side_by_side_video: {path: $combined_video, sha256: $combined_video_sha256}
    },
    claim_boundary: "MATTERIX executes a simulator projection of the selected heater setpoint and dwell through the admitted provider task. It does not execute the full physical route or load the generated Dynamical OpenUSD stage."
  }' >"$FINAL_JSON" || fail "final JSON generation failed"

printf '%s\n' "$FINAL_JSON"
