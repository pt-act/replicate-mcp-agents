"""Example: Latitude integration for prompt management and tracing.

This example demonstrates how to use the Latitude integration to:
    1. Fetch prompts from Latitude
    2. Run prompts via Latitude API
    3. Manage prompt versions
    4. Continue conversations
    5. Automatically trace agent executions

Prerequisites:
    pip install "replicate-mcp-agents[latitude]"

Environment Variables:
    LATITUDE_API_KEY     — Your Latitude API key
    LATITUDE_PROJECT_ID  — Your Latitude project ID
    REPLICATE_API_TOKEN  — Your Replicate API token

API Documentation:
    https://gateway.latitude.so/api-docs/
"""

from __future__ import annotations

import asyncio
import os

from replicate_mcp import (
    AgentBuilder,
    LatitudeClient,
    LatitudeConfig,
    LatitudePlugin,
    PluginRegistry,
    agent,
)


async def example_fetch_prompt():
    """Fetch a prompt from Latitude using the documents API."""
    print("=== Fetch Prompt ===")

    config = LatitudeConfig()
    if not config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    client = LatitudeClient(config)

    async with client:
        # Fetch a prompt from the live version (default)
        try:
            # Per API: GET /projects/{projectId}/versions/{versionUuid}/documents/{path}
            prompt = await client.get_prompt("path/to/document", version_uuid="live")
            print(f"Fetched prompt: {prompt.name}")
            print(f"Content preview: {prompt.content[:100]}...")
            print(f"Config: {prompt.config}")
        except Exception as e:
            print(f"Failed to fetch prompt: {e}")


async def example_run_prompt():
    """Execute a prompt via Latitude API."""
    print("\n=== Run Prompt ===")

    config = LatitudeConfig()
    if not config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    client = LatitudeClient(config)

    async with client:
        try:
            # Run a prompt with parameters
            # Per API: POST /projects/{projectId}/versions/{versionUuid}/documents/run
            result = await client.run_prompt(
                path="greeting",
                parameters={"name": "World"},
                stream=False,  # Set True for SSE streaming
                version_uuid="live",
            )
            print(f"Conversation UUID: {result.get('uuid')}")
            print(f"Response: {result.get('response', {}).get('text', 'N/A')[:100]}...")
            print(f"Usage: {result.get('response', {}).get('usage', {})}")
            print(f"Cost: ${result.get('response', {}).get('cost', 0)}")
        except Exception as e:
            print(f"Failed to run prompt: {e}")


async def example_conversation():
    """Continue a multi-turn conversation."""
    print("\n=== Conversation Chat ===")

    config = LatitudeConfig()
    if not config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    client = LatitudeClient(config)

    async with client:
        # First, run a prompt to get a conversation UUID
        try:
            result = await client.run_prompt(
                path="chat-assistant",
                parameters={"topic": "Python"},
                stream=False,
            )
            conversation_uuid = result.get("uuid")
            print(f"Started conversation: {conversation_uuid}")

            # Continue the conversation
            # Per API: POST /conversations/{conversationUuid}/chat
            chat_result = await client.chat(
                conversation_uuid=conversation_uuid,
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Tell me more about async/await"}]
                    }
                ],
                stream=False,
            )
            print(f"Response: {chat_result.get('response', {}).get('text', 'N/A')[:100]}...")

            # Get conversation history
            # Per API: GET /conversations/{conversationUuid}
            history = await client.get_conversation(conversation_uuid)
            print(f"Total messages: {len(history.get('conversation', []))}")

        except Exception as e:
            print(f"Conversation error: {e}")


async def example_version_management():
    """Create and publish prompt versions."""
    print("\n=== Version Management ===")

    config = LatitudeConfig()
    if not config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    client = LatitudeClient(config)

    async with client:
        try:
            # Create a new draft version
            # Per API: POST /projects/{projectId}/versions
            version = await client.create_version(name="Feature: Add Spanish support")
            print(f"Created version: {version.get('uuid')}")
            print(f"Status: {version.get('status')}")

            # Create/update a prompt in this draft version
            # Per API: POST /projects/{projectId}/versions/{versionUuid}/documents/create-or-update
            prompt = await client.create_or_update_prompt(
                path="greeting",
                content="---\nprovider: openai\nmodel: gpt-4\n---\n\nHola {{name}}!",
                version_uuid=version.get("uuid"),
            )
            print(f"Created prompt in draft: {prompt.name}")

            # Publish the version to make it live
            # Per API: POST /projects/{projectId}/versions/{versionUuid}/publish
            published = await client.publish_version(
                version_uuid=version.get("uuid"),
                title="Spanish Support",
                description="Added Spanish greeting support",
            )
            print(f"Published version: {published.get('status')}")

        except Exception as e:
            print(f"Version management error: {e}")


async def example_tracing():
    """Manual tracing of agent executions."""
    print("\n=== Tracing ===")

    config = LatitudeConfig()
    if not config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    client = LatitudeClient(config)

    async with client:
        # Trace an agent execution manually
        with client.trace("agent-run", agent_id="greeting-agent") as trace:
            # Simulate agent execution
            trace.input_data = {"name": "World"}

            await asyncio.sleep(0.1)
            output = {"message": "Hello, World!"}

            trace.record_result(output, latency_ms=100, cost_usd=0.001)
            print(f"Traced execution: {trace.id[:8]}...")
            print(f"Success: {trace.success}, Latency: {trace.latency_ms}ms, Cost: ${trace.cost_usd}")


async def example_plugin_integration():
    """Use LatitudePlugin for automatic tracing of all agent runs."""
    print("\n=== Plugin Integration ===")

    lat_config = LatitudeConfig()
    if not lat_config.is_configured:
        print("Set LATITUDE_API_KEY and LATITUDE_PROJECT_ID environment variables")
        return

    lat_plugin = LatitudePlugin(lat_config)

    # Register with the plugin system
    registry = PluginRegistry()
    registry.load(lat_plugin)

    # Define an agent
    @agent(model="meta/llama-3-8b-instruct", description="Greeting agent")
    def greeter(name: str) -> dict:
        return {"prompt": f"Greet {name} warmly"}

    print("Agent defined — executions will be traced to Latitude automatically")

    # Cleanup
    registry.unload_all()


async def main():
    """Run all examples."""
    await example_fetch_prompt()
    await example_run_prompt()
    await example_conversation()
    await example_version_management()
    await example_tracing()
    await example_plugin_integration()


if __name__ == "__main__":
    asyncio.run(main())
