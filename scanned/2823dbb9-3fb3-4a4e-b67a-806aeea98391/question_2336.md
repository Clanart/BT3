# Q2336: `bridge_circuit_constant` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `bridge_circuit_constant` in `core/src/config/protocol.rs` from a fund-moving path with input violating an invariant `bridge_circuit_constant` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `core/src/config/protocol.rs` -> `bridge_circuit_constant`
- Entrypoint: an aggregator request or on-chain value -> `bridge_circuit_constant`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `bridge_circuit_constant` on a fund-moving path validates the invariant `bridge_circuit_constant` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `bridge_circuit_constant` with invariant-violating input and assert it fails closed
