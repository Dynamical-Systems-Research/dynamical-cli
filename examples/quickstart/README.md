# Simulator quickstart

This example transfers one sample. It then runs one ultrasonic conditioning
operation within set limits. A simulator provider runs software only. It does
not operate hardware.

Run these commands from this directory:

```bash
dynamical capabilities --operation transfer-sample --json
dynamical capabilities --operation condition-ultrasonic --json
dynamical compose requirement.yaml -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
```

The selected providers supply simulator evidence within their declared limits.
A valid trace shows that the virtual steps ran as declared. It also records the
source of each step. It does not show a physical conditioning effect. It does
not authorize hardware. It does not operate hardware.
