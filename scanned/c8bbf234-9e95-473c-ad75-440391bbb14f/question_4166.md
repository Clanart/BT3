# Q4166: `output_stream_ended_prematurely` and concurrent request handling

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port issue concurrent calls to `output_stream_ended_prematurely` in `core/src/rpc/error.rs` that interleave inside a shared mutable structure (nonce map, session table, in-flight deposit state, database transaction) so one call observes state another call is mid-way through mutating, and a signature, connector or index is issued twice?

## Target
- File/function: `core/src/rpc/error.rs` -> `output_stream_ended_prematurely`
- Entrypoint: concurrent gRPC requests to the open port -> `output_stream_ended_prematurely`
- Attacker controls: request concurrency and timing; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: double-issue a single-use protocol resource
- Invariant to test: single-use protocol resources are issued at most once under arbitrary interleaving
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: hammer `output_stream_ended_prematurely` concurrently and assert no resource is issued twice
