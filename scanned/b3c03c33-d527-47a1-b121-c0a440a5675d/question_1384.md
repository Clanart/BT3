# Q1384: `to_sat_per_kvb` and protocol parameter boundaries

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose deposit or withdrawal values that hit a boundary of the parameters `to_sat_per_kvb` in `crates/clementine-primitives/src/lib.rs` supplies (`NUMBER_OF_ASSERT_TXS`, amounts, timelocks, counts) so a derived value overflows, truncates, or rounds in a way that lets bridged value leave a vault unmatched?

## Target
- File/function: `crates/clementine-primitives/src/lib.rs` -> `to_sat_per_kvb` (Primitive types shared across clementine crates)
- Entrypoint: a deposit or withdrawal shaped to hit the boundary -> `to_sat_per_kvb`
- Attacker controls: the amounts and indices in the attacker's own deposit or withdrawal; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit an arithmetic boundary in derived protocol values
- Invariant to test: every derived value is exact across the full domain of protocol parameters
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: evaluate `to_sat_per_kvb` across boundary parameters and assert exactness
