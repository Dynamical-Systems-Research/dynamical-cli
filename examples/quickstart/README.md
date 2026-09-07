# Simulator quickstart

This example records a 35 °C temperature setpoint and a 30 s ultrasound command
for one sample in the stationary SDL1 well. These settings follow the
[upstream recipe](https://github.com/AccelerationConsortium/SDL1_OpenTron_electrodeposition/blob/2c5a911/example/main.py#L134-L141).
The adapter records commands only; it does not operate hardware or predict
the resulting temperature or conditioning effect.

Run these commands from this directory:

```bash
dynamical capabilities --facility sdl1 --operation condition-ultrasonic --json
dynamical compose requirement.yaml --facility sdl1 -o composition.json
dynamical compile composition.json -o compiled-world
dynamical run compiled-world -o trace.ndjson
dynamical validate trace.ndjson --json
dynamical run trace.ndjson --mode replay -o replay.ndjson
dynamical validate replay.ndjson --json
```

A valid trace proves command bookkeeping and replay consistency. Applied
parameters and observed temperature remain unavailable. A requirement for
measured temperature or a physical conditioning effect cannot pass on this
record. Full SDL1 response calibration remains a release prerequisite.
