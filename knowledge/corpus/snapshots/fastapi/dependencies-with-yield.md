# FastAPI Dependencies with yield

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `fastapi/fastapi`, `docs/en/docs/tutorial/dependencies/dependencies-with-yield.md`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## Resource lifecycle

A dependency can use `yield` to provide a resource and run cleanup code afterward. Code before
`yield` acquires or prepares the resource; code in `finally` releases it. FastAPI implements this
behavior with context-manager semantics.

## Request scope

The default `scope="request"` runs cleanup after the response is sent. This scope permits a
dependency with `yield` to depend on another request-scoped yield dependency because teardown can
occur in reverse dependency order.

## Function scope

With `Depends(scope="function")`, cleanup runs after the path-operation function returns but before
the response is sent. A request-scoped yield dependency cannot depend on a function-scoped yield
dependency because the inner resource would already be closed before the outer cleanup executes.

## Exceptions and cleanup

Use `try`/`finally` around `yield` for cleanup. If application-specific exceptions are caught in the
dependency, they should normally be re-raised unless deliberately translated; swallowing an
exception can hide failure semantics while still continuing response handling.

## Background task boundary

Resources created by a yield dependency should not be kept alive only for a background task. A
background task should create and close its own resource, passing stable identifiers rather than a
request-scoped session object when appropriate.
