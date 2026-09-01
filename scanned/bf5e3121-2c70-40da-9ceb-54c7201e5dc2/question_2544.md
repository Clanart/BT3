# Q2544: `taproot_builder_with_scripts` and network/address validation

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply an address or key that `taproot_builder_with_scripts` in `crates/clementine-utils/src/address.rs` accepts for the wrong Bitcoin network (an `assume_checked` conversion, a network-agnostic parse), so a bridge output is built for a network whose spend path the attacker controls or nobody does?

## Target
- File/function: `crates/clementine-utils/src/address.rs` -> `taproot_builder_with_scripts`
- Entrypoint: a deposit or withdrawal request carrying a foreign-network address -> `taproot_builder_with_scripts`
- Attacker controls: the address string in the request; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: have a bridge output built to a script valid on the wrong network
- Invariant to test: every address used to build a bridge output validates against the deployment's network
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: submit foreign-network addresses and assert rejection
