# Q2798: `get_next_height_to_process` and scoping of rows to one deposit

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port make `get_next_height_to_process` in `core/src/database/state_machine.rs` return or write a row belonging to a different deposit, round or operator - a query missing a scoping predicate, a key derived only from a txid or index - so material from one vault is used to act on another?

## Target
- File/function: `core/src/database/state_machine.rs` -> `get_next_height_to_process` (This module includes database functions for persisting and loading state machines)
- Entrypoint: aggregator request naming a chosen deposit -> `get_next_height_to_process`
- Attacker controls: the deposit outpoint, txid or index in the request; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: use one vault's material against another vault
- Invariant to test: every row `get_next_height_to_process` returns belongs to the deposit the caller is authorised for
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: query with a foreign deposit key and assert no rows cross the boundary
