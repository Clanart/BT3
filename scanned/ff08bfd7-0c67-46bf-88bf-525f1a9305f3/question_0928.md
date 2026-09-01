# Q0928: `calculate_taproot_leaf_depths` and protocol parameter boundaries

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port choose deposit or withdrawal values that hit a boundary of the parameters `calculate_taproot_leaf_depths` in `crates/clementine-utils/src/address.rs` supplies (`the module's protocol constant`, amounts, timelocks, counts) so a derived value overflows, truncates, or rounds in a way that lets bridged value leave a vault unmatched?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `calculate_taproot_leaf_depths`
- Entrypoint: a deposit or withdrawal shaped to hit the boundary -> `calculate_taproot_leaf_depths`
- Attacker controls: the amounts and indices in the attacker's own deposit or withdrawal; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: exploit an arithmetic boundary in derived protocol values
- Invariant to test: every derived value is exact across the full domain of protocol parameters
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: evaluate `calculate_taproot_leaf_depths` across boundary parameters and assert exactness
