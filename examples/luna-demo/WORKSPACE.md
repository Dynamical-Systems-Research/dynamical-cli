# Disposable Dynamical workspace

You can create requirements, analysis, compiled artifacts, traces, figures, and
notes in this directory. Use `--help` to inspect the installed interface:

```bash
dynamical --help
dynamical capabilities --help
dynamical compose --help
dynamical compile --help
dynamical run --help
dynamical validate --help
```

The five public commands are `capabilities`, `compose`, `compile`, `run`, and
`validate`. Do not assume flags, providers, or artifact formats that are not in
their help output.

For a long command, start a generic background job and poll its log in later
`bash` calls:

```bash
mkdir -p job
nohup bash -lc 'LONG_COMMAND; code=$?; printf "%s\n" "$code" > job/status.tmp; mv job/status.tmp job/status' > job/run.log 2>&1 </dev/null &
echo $! > job/pid
```

```bash
if test -f job/status; then cat job/status; else tail -80 job/run.log; fi
```

Process liveness is not evidence. Use Dynamical validation results before you
cite an artifact. Scientific values are in `observation` events under
`observation.channels` in the returned NDJSON trace.

Preserve each `HOLD` receipt. If its reasons identify an incomplete or invalid
requirement, you can author a corrected requirement without changing admission
or authority. If no admitted route exists, preserve the `HOLD`.

Write the final agent decision as valid JSON in `decision.json`. It must contain
exactly these fields:

- `selected_virtual_campaign`: a relative path to its requirement file.
- `physical_route_requirement`: a relative path to the requested physical
  requirement file.
- `selected_physical_experiment`: an object with `operation`, `conditions`,
  `parameters`, and `measurements` fields. `operation` must name a step in the
  physical requirement. `conditions` must map every valued physical-requirement
  input ID to its value. `parameters` must map each selected-step parameter to
  `{ "value": ..., "unit": ... }`. `measurements` must list the physical proof
  output-port IDs.
- `decision_rationale`: a string.
- `uncertainty`: an array of strings.
- `submitted`: `false`.

Do not add hashes, receipts, evidence paths, route status, provider bindings, or
chronology claims. Dynamical will compose the requested route and attach its own
validation and evidence. Do not submit physical work. Preserve any `HOLD`
returned by Dynamical, and do not claim that virtual evidence is physical proof.
