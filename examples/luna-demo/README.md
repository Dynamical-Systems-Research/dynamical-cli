# Luna demo

Reference agent: GPT-5.6 Luna.

This example is a small OpenAI Responses API loop around four Pi-style tools:
`read`, `write`, `edit`, and `bash`. It does not set a research policy or perform
facility setup, validation, submission, or cleanup.

The container route is canonical. It installs the v0.1 wheel and the pinned demo
requirement. It copies only the runner, prompt, and objective into the image. It
does not copy repository source.

Build once from the repository root. The `wheel` build context must contain the
v0.1 wheel:

```bash
docker build -f examples/luna-demo/Dockerfile \
  --build-context wheel=dist \
  --build-context demo=examples/luna-demo \
  -t dynamical-luna-demo:0.1 .
```

Set `demo_workspace` to a caller-prepared disposable directory that contains
`WORKSPACE.md`. Run once with only that directory mounted read-write:

```bash
docker run --rm \
  --read-only \
  --user "$(id -u):$(id -g)" \
  --cap-drop=ALL \
  --security-opt no-new-privileges \
  --pids-limit 512 \
  --mount "type=bind,src=${demo_workspace},dst=/workspace" \
  --env OPENAI_API_KEY \
  dynamical-luna-demo:0.1
```

The runner prints each OpenAI stream event as JSON. It also prints each tool call
and result. `bash` is unrestricted only inside the disposable virtual workspace.
The container has a read-only root. Do not mount hardware, a Docker socket, or a
physical-provider directory. The disposable directory contains all agent-authored
files.
