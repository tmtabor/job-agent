"""Job-agent's two LLM call sites, both single-shot (no tool-calling):

- scoring: `agent`, `AgentDeps`, `score_posting`, `build_scoring_input` — Tier
  2 fit evaluation of one posting (agent/agents/single.py).
- company_research: `company_research_agent`, `CompanyResearchDeps`,
  `research_company`, `build_research_input` — per-company research synthesis
  (agent/agents/company_research.py).
"""

from agent.agents.company_research import (
    CompanyResearchDeps,
    build_research_input,
    company_research_agent,
    research_company,
)
from agent.agents.single import AgentDeps, agent, build_scoring_input, score_posting

__all__ = [
    "AgentDeps",
    "CompanyResearchDeps",
    "agent",
    "build_research_input",
    "build_scoring_input",
    "company_research_agent",
    "research_company",
    "score_posting",
]
