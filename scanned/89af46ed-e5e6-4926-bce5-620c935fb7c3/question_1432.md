# Q1432: `op_return_txout` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `op_return_txout` in `crates/clementine-utils/src/address.rs` from a fund-moving path with input violating an invariant `op_return_txout` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `op_return_txout`
- Entrypoint: an aggregator request or on-chain value -> `op_return_txout`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `op_return_txout` on a fund-moving path validates the invariant `op_return_txout` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `op_return_txout` with invariant-violating input and assert it fails closed
