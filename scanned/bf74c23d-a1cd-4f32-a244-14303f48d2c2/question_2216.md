# Q2216: `create_taproot_address` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `create_taproot_address` in `crates/clementine-utils/src/address.rs` from a fund-moving path with input violating an invariant `create_taproot_address` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `create_taproot_address`
- Entrypoint: an aggregator request or on-chain value -> `create_taproot_address`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `create_taproot_address` on a fund-moving path validates the invariant `create_taproot_address` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `create_taproot_address` with invariant-violating input and assert it fails closed
