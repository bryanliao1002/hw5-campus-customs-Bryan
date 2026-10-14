"""Five PydanticAI agents with Portkey-only model configuration and bounded delegation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from backend.models import AgentDependencies, AgentReport, AgentRole


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "backend" / "prompts"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
REQUIRED_MODEL = "gpt-6-luna"
ROLES: tuple[AgentRole, ...] = (
    "boss", "inventory", "accounting", "facilities", "customer_service"
)


def create_portkey_model() -> OpenAIChatModel:
    """Create the sole application model, authenticated only through Portkey."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    api_key = os.environ.get("PORTKEY_API_KEY")
    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY must be set in the environment or root .env")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-api-key": api_key},
    )
    provider = OpenAIProvider(openai_client=client)
    # Portkey's gpt-6-luna Chat Completions route supports function tools when
    # reasoning_effort is explicitly disabled. PydanticAI otherwise infers a
    # reasoning setting for this model, which the routed endpoint rejects.
    return OpenAIChatModel(
        REQUIRED_MODEL,
        provider=provider,
        settings={"openai_reasoning_effort": "none"},
    )


def build_agents(
    model: OpenAIChatModel, mcp_toolset: object
) -> dict[AgentRole, Agent[AgentDependencies, AgentReport]]:
    agents: dict[AgentRole, Agent[AgentDependencies, AgentReport]] = {}
    for role in ROLES:
        prompt_file = PROMPTS_DIR / f"{role}.md"
        if not prompt_file.is_file():
            raise FileNotFoundError(f"Agent prompt is missing: {prompt_file}")
        prompt = prompt_file.read_text(encoding="utf-8").strip()
        agents[role] = Agent(
            model,
            name=f"campus_customs_{role}",
            deps_type=AgentDependencies,
            output_type=AgentReport,
            instructions=prompt,
            toolsets=[mcp_toolset],
        )

    for owner, agent in agents.items():
        def make_peer_tool(agent_role: AgentRole):
            async def delegate_to_peer(
                ctx: RunContext[AgentDependencies],
                target_agent: AgentRole,
                task: str,
            ) -> AgentReport:
                """Ask any other specialist for bounded help on this ticket."""
                if target_agent == agent_role:
                    raise ValueError("Agents may delegate to any peer except themselves")
                if not task.strip() or len(task) > 2000:
                    raise ValueError("Delegation task must contain 1 to 2000 characters")
                coordinator = ctx.deps.coordinator
                return await coordinator.delegate(
                    parent_role=agent_role,
                    target_role=target_agent,
                    task=task.strip(),
                    parent_deps=ctx.deps,
                )

            return delegate_to_peer

        agent.tool(make_peer_tool(owner))
    return agents
