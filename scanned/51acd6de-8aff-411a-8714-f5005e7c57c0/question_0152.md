# Q0152: `convert_hex_string_to_bytes` and protocol parameter boundaries

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose deposit or withdrawal values that hit a boundary of the parameters `convert_hex_string_to_bytes` in `core/src/config/protocol.rs` supplies (`MAINNET_BRIDGE_CIRCUIT_CONSTANT`, amounts, timelocks, counts) so a derived value overflows, truncates, or rounds in a way that lets bridged value leave a vault unmatched?

## Target
- File/function: `core/src/config/protocol.rs` -> `convert_hex_string_to_bytes`
- Entrypoint: a deposit or withdrawal shaped to hit the boundary -> `convert_hex_string_to_bytes`
- Attacker controls: the amounts and indices in the attacker's own deposit or withdrawal; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit an arithmetic boundary in derived protocol values
- Invariant to test: every derived value is exact across the full domain of protocol parameters
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: evaluate `convert_hex_string_to_bytes` across boundary parameters and assert exactness
