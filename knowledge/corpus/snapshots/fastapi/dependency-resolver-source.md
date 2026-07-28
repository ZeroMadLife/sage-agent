# FastAPI Dependency Resolver Source Contract

> Approved extractive source snapshot for Sage RAG evaluation.
> Upstream: `fastapi/fastapi`, `fastapi/dependencies/utils.py`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## Resolver entrypoint

`solve_dependencies` is the asynchronous resolver entrypoint. It receives the request, a
`Dependant` graph, body/background-task/response context, an optional dependency cache, and an
`AsyncExitStack`. It returns a structured result containing resolved values and validation errors.

## Recursive dependency solving

The resolver walks sub-dependencies recursively. It can apply dependency overrides, propagate the
same request-scoped context, and resolve generator, coroutine, or threadpool-backed callables through
the appropriate execution path.

## Per-request dependency cache

When no cache is provided, `solve_dependencies` creates a new dictionary for that resolution. If a
sub-dependency has `use_cache=True` and its cache key is present, the resolver reuses the stored
value. Otherwise it executes the dependency and stores the result under its cache key. This is a
resolution-scoped cache, not a process-wide permanent cache.

## Result propagation

Resolved sub-dependency values are assigned to the parent dependency arguments. Validation errors,
background tasks, response data, and the updated dependency cache are propagated in the resolver
result so later dependency steps and the path operation share one coherent request context.
