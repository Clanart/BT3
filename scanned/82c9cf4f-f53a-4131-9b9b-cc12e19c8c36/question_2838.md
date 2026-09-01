# Q2838: `nonce_gen` and concurrent request handling

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port issue concurrent calls to `nonce_gen` in `core/src/rpc/verifier.rs` that interleave inside a shared mutable structure (nonce map, session table, in-flight deposit state, database transaction) so one call observes state another call is mid-way through mutating, and a signature, connector or index is issued twice?

## Target
- File/function: `core/src/rpc/verifier.rs` -> `nonce_gen`
- Entrypoint: concurrent gRPC requests to the open port -> `nonce_gen`
- Attacker controls: request concurrency and timing; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: double-issue a single-use protocol resource
- Invariant to test: single-use protocol resources are issued at most once under arbitrary interleaving
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: hammer `nonce_gen` concurrently and assert no resource is issued twice
