# FastAPI Dependencies

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `fastapi/fastapi`, `docs/en/docs/tutorial/dependencies/index.md`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## Dependency injection

FastAPI dependency injection lets a path-operation function declare values it requires. FastAPI
calls the dependency and injects its result. Common uses include shared logic, database
connections, authentication, and authorization while reducing repeated code.

## Declaring Depends

Pass the dependency callable to `Depends` without calling it. The dependency can accept the same
kinds of parameters as a path-operation function. The recommended modern form combines
`typing.Annotated` with `Depends` so type information remains available to editors and type
checkers.

## Async and sync

Dependencies and path-operation functions may independently use `def` or `async def`. FastAPI
coordinates the calls, so an async dependency can be used by a sync path operation and the reverse
combination is also supported.

## OpenAPI integration

Request declarations, validation, and requirements contributed by dependencies and
sub-dependencies are integrated into the generated OpenAPI schema. The dependency tree therefore
affects both runtime resolution and API documentation.

## Dependency tree and caching

Dependencies can depend on other dependencies. FastAPI builds and resolves the dependency tree.
When the same dependency is required more than once in one request, its result is reused by
default; `use_cache=False` requests another call.
