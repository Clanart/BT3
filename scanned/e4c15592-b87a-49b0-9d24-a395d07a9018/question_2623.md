# Q2623: `begin_transaction` and null/absent columns

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port cause `begin_transaction` in `core/src/database/mod.rs` to read a null or default where the protocol requires a concrete value (an unattributed payout, a missing block hash, an absent operator key), so a downstream check silently passes or a bridge UTXO becomes unreachable?

## Target
- File/function: `core/src/database/mod.rs` -> `begin_transaction` (Database crate provides functions that adds/reads values from PostgreSQL)
- Entrypoint: attacker-shaped on-chain data -> `begin_transaction`
- Attacker controls: the on-chain data that leaves the column unset; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: turn an absent value into a passing check or a dead end
- Invariant to test: every column a fund-moving path reads is non-null by construction at that point
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: force the null case and assert `begin_transaction` fails closed
