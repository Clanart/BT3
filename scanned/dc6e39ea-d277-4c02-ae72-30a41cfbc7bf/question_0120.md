# Q0120: `try_parse_file` and protocol parameter boundaries

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose deposit or withdrawal values that hit a boundary of the parameters `try_parse_file` in `core/src/config/mod.rs` supplies (`the module's protocol constant`, amounts, timelocks, counts) so a derived value overflows, truncates, or rounds in a way that lets bridged value leave a vault unmatched?

## Target
- File/function: `core/src/config/mod.rs` -> `try_parse_file` (This module defines configuration options)
- Entrypoint: a deposit or withdrawal shaped to hit the boundary -> `try_parse_file`
- Attacker controls: the amounts and indices in the attacker's own deposit or withdrawal; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit an arithmetic boundary in derived protocol values
- Invariant to test: every derived value is exact across the full domain of protocol parameters
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: evaluate `try_parse_file` across boundary parameters and assert exactness
