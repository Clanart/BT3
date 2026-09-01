# Q1159: `get_deposit_data_with_move_tx` and null/absent columns

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port cause `get_deposit_data_with_move_tx` in `core/src/database/operator.rs` to read a null or default where the protocol requires a concrete value (an unattributed payout, a missing block hash, an absent operator key), so a downstream check silently passes or a bridge UTXO becomes unreachable?

## Target
- File/function: `core/src/database/operator.rs` -> `get_deposit_data_with_move_tx` (This module includes database functions which are mainly used by an operator)
- Entrypoint: attacker-shaped on-chain data -> `get_deposit_data_with_move_tx`
- Attacker controls: the on-chain data that leaves the column unset; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: turn an absent value into a passing check or a dead end
- Invariant to test: every column a fund-moving path reads is non-null by construction at that point
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: force the null case and assert `get_deposit_data_with_move_tx` fails closed
