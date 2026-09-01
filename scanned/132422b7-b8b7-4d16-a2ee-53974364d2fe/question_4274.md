# Q4274: `create_operator_grpc_server` and concurrent request handling

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port issue concurrent calls to `create_operator_grpc_server` in `core/src/servers.rs` that interleave inside a shared mutable structure (nonce map, session table, in-flight deposit state, database transaction) so one call observes state another call is mid-way through mutating, and a signature, connector or index is issued twice?

## Target
- File/function: `core/src/servers.rs` -> `create_operator_grpc_server` (Utilities for operator and verifier servers)
- Entrypoint: concurrent gRPC requests to the open port -> `create_operator_grpc_server`
- Attacker controls: request concurrency and timing; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: double-issue a single-use protocol resource
- Invariant to test: single-use protocol resources are issued at most once under arbitrary interleaving
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: hammer `create_operator_grpc_server` concurrently and assert no resource is issued twice
