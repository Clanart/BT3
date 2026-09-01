# Q1392: `to_sat_per_kwu_ceil` and network/address validation

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply an address or key that `to_sat_per_kwu_ceil` in `crates/clementine-primitives/src/lib.rs` accepts for the wrong Bitcoin network (an `assume_checked` conversion, a network-agnostic parse), so a bridge output is built for a network whose spend path the attacker controls or nobody does?

## Target
- File/function: `crates/clementine-primitives/src/lib.rs` -> `to_sat_per_kwu_ceil` (Primitive types shared across clementine crates)
- Entrypoint: a deposit or withdrawal request carrying a foreign-network address -> `to_sat_per_kwu_ceil`
- Attacker controls: the address string in the request; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: have a bridge output built to a script valid on the wrong network
- Invariant to test: every address used to build a bridge output validates against the deployment's network
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: submit foreign-network addresses and assert rejection
