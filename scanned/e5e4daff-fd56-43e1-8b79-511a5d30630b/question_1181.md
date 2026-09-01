# Q1181: `output_stream_ended_prematurely` and caller-influenced session identity

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port influence the nonce/signing session identity used by `output_stream_ended_prematurely` in `core/src/rpc/error.rs` - reusing an id, guessing one, or forcing eviction of a live session via `remove_oldest_session` - so nonces or partial signatures from one session are consumed by another?

## Target
- File/function: `core/src/rpc/error.rs` -> `output_stream_ended_prematurely`
- Entrypoint: repeated gRPC requests to the open port -> `output_stream_ended_prematurely`
- Attacker controls: request rate, concurrency and any session identifier in the message; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: cross-wire two signing sessions
- Invariant to test: a session's nonces are consumed only by the signing round that created it
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: open overlapping sessions and assert no nonce crosses session boundaries
