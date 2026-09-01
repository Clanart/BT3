# Q0024: `is_compatible` and network/address validation

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port supply an address or key that `is_compatible` in `core/src/compatibility.rs` accepts for the wrong Bitcoin network (an `assume_checked` conversion, a network-agnostic parse), so a bridge output is built for a network whose spend path the attacker controls or nobody does?

## Target
- File/function: `core/src/compatibility.rs` -> `is_compatible` (This module contains the logic for checking compatibility between actors in the system)
- Entrypoint: a deposit or withdrawal request carrying a foreign-network address -> `is_compatible`
- Attacker controls: the address string in the request; attacker is an unprivileged party using only the deployment's shipped defaults; holds no role or key
- Exploit idea: have a bridge output built to a script valid on the wrong network
- Invariant to test: every address used to build a bridge output validates against the deployment's network
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: submit foreign-network addresses and assert rejection
