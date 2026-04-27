# Workflow Authoring Guide

This guide shows how to define declarative workflows that orchestrate multiple Replicate-backed agents.

## Workflow Schema

Workflows are described in YAML under `examples/workflows/` and support the following keys:

- `name`: Unique identifier for the workflow.
- `description`: Short explanation of the workflow purpose.
- `agents`: List of agents participating in the workflow.
- `edges`: Directed edges describing data flow between agents.

Refer to [`content_pipeline.yaml`](../examples/workflows/content_pipeline.yaml) for a complete example.

## Loading Workflows

Workflows will be discoverable via the CLI once the registry subsystem is implemented:

```bash
poetry run replicate-agent workflows list
```

## Checkpointing

Checkpoint data is stored under `~/.replicate/sessions/`. Resume a workflow using:

```bash
poetry run replicate-agent resume <session-id> --from-step <step-number>
```

Implementation of the resume command will arrive in future iterations of the project scaffold.