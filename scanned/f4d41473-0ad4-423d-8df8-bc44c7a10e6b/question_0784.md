# Q0784: `is_block_finalized` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `is_block_finalized` in `crates/clementine-config/src/protocol.rs` from a fund-moving path with input violating an invariant `is_block_finalized` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `is_block_finalized` (Protocol parameters for the clementine bridge)
- Entrypoint: an aggregator request or on-chain value -> `is_block_finalized`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `is_block_finalized` on a fund-moving path validates the invariant `is_block_finalized` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `is_block_finalized` with invariant-violating input and assert it fails closed
