# Q1256: `from_toml_file` and an assumption its callers do not enforce

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port reach `from_toml_file` in `crates/clementine-config/src/protocol.rs` from a fund-moving path with input violating an invariant `from_toml_file` documents but does not check (a length, a range, a canonical form), so a bridge transaction is built from a value no caller validated?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `from_toml_file` (Protocol parameters for the clementine bridge)
- Entrypoint: an aggregator request or on-chain value -> `from_toml_file`
- Attacker controls: the value flowing into the helper; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit a validation gap between a helper and its callers
- Invariant to test: every caller of `from_toml_file` on a fund-moving path validates the invariant `from_toml_file` assumes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: call `from_toml_file` with invariant-violating input and assert it fails closed
