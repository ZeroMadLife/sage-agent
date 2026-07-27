# LangGraph Overview

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `langchain-ai/docs`, `src/oss/langgraph/overview.mdx`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## Orchestration runtime

LangGraph is a low-level orchestration runtime for building long-running, stateful agents. It
does not prescribe prompts or an agent architecture. LangChain agents provide higher-level,
prebuilt architectures, while LangGraph exposes lower-level control over state and execution.

## Core benefits

The official overview names durable execution, streaming, human-in-the-loop control, and
persistence as central capabilities. Durable execution lets a workflow resume after failures;
human-in-the-loop support lets an operator inspect and modify state at controlled points.

## Installation and minimal graph

The Python package is installed with `pip install -U langgraph`. A minimal graph uses
`StateGraph`, adds nodes and edges between `START` and `END`, compiles the graph, and invokes it
with state. LangGraph is the runtime that executes this graph; it is not itself the model.

## Boundary with LangChain

Use LangChain agents when a prebuilt tool-calling loop is sufficient. Use LangGraph when the
application needs explicit orchestration, persistence, streaming, or human approval semantics.
The two projects can be used together, but their responsibilities are different.
