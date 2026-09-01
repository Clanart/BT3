# Q2541: `output_stream_ended_prematurely` and re-running protocol setup

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port invoke `output_stream_ended_prematurely` in `core/src/rpc/error.rs` after the protocol is live, causing keys, operator registrations, round state or collateral bookkeeping to be re-derived or overwritten, so existing vaults reference state that no longer matches the entities holding them?

## Target
- File/function: `core/src/rpc/error.rs` -> `output_stream_ended_prematurely`
- Entrypoint: a gRPC request to the open port -> `output_stream_ended_prematurely`
- Attacker controls: the timing of the call relative to live deposits; attacker is an unprivileged network client who can reach the public gRPC port; holds no certificate, role or key
- Exploit idea: invalidate live vault state by forcing a re-setup
- Invariant to test: setup-time state is immutable once a deposit references it
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: call `output_stream_ended_prematurely` after a deposit exists and assert live state is untouched
