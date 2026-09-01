# Q2488: `from_toml_file` and protocol parameter boundaries

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose deposit or withdrawal values that hit a boundary of the parameters `from_toml_file` in `crates/clementine-config/src/protocol.rs` supplies (`WINTERNITZ_LOG_D`, amounts, timelocks, counts) so a derived value overflows, truncates, or rounds in a way that lets bridged value leave a vault unmatched?

## Target
- File/function: `crates/clementine-config/src/protocol.rs` -> `from_toml_file` (Protocol parameters for the clementine bridge)
- Entrypoint: a deposit or withdrawal shaped to hit the boundary -> `from_toml_file`
- Attacker controls: the amounts and indices in the attacker's own deposit or withdrawal; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit an arithmetic boundary in derived protocol values
- Invariant to test: every derived value is exact across the full domain of protocol parameters
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: evaluate `from_toml_file` across boundary parameters and assert exactness
