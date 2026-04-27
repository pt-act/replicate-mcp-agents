# `replicate_mcp.sdk` — Fluent Python SDK

::: replicate_mcp.sdk
    options:
      members:
        - agent
        - get_default_registry
        - reset_default_registry
        - AgentBuilder
        - WorkflowBuilder
        - WorkflowSpec
        - WorkflowStep
        - AgentContext

## Overview

The SDK module provides two complementary APIs for defining agents:

### Decorator API

```python
from replicate_mcp.sdk import agent

@agent(model="meta/llama-3-8b-instruct", tags=["chat"])
def llama(prompt: str) -> dict:
    """Fast chat completion with LLaMA 3."""
    return {"prompt": prompt}
```

### Builder API

```python
from replicate_mcp.sdk import AgentBuilder

spec = (
    AgentBuilder("summariser")
    .model("mistral/mixtral-8x7b-instruct")
    .description("Summarise long documents")
    .tag("nlp")
    .streaming(True)
    .estimated_cost(0.003)
    .build()
)
```

### Workflow builder

```python
from replicate_mcp.sdk import WorkflowBuilder

pipeline = (
    WorkflowBuilder("rag-pipeline")
    .description("Retrieval-augmented generation")
    .then("embed", input_map={"text": "query"})
    .then("retrieve", input_map={"embedding": "output"})
    .then("generate", condition="len(docs) > 0")
    .build()
)
```

### Test isolation with `AgentContext`

```python
from replicate_mcp.sdk import agent, AgentContext

with AgentContext() as ctx:
    @agent  # Registers into ctx.registry, not the global default
    def test_model() -> dict:
        return {}

    assert ctx.registry.has("test_model")
# Global registry is restored on exit
```