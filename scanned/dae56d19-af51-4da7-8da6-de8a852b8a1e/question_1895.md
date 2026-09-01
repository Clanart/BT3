# Q1895: `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` and scoping of rows to one deposit

## Question
Can any unprivileged party who can open a TCP connection to the aggregator's gRPC port make `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` in `core/src/database/verifier.rs` return or write a row belonging to a different deposit, round or operator - a query missing a scoping predicate, a key derived only from a txid or index - so material from one vault is used to act on another?

## Target
- File/function: `core/src/database/verifier.rs` -> `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` (This module includes database functions which are mainly used by a verifier)
- Entrypoint: aggregator request naming a chosen deposit -> `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id`
- Attacker controls: the deposit outpoint, txid or index in the request; attacker is an unprivileged network client whose requests and on-chain actions drive persistence; holds no role or key
- Exploit idea: use one vault's material against another vault
- Invariant to test: every row `get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id` returns belongs to the deposit the caller is authorised for
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: query with a foreign deposit key and assert no rows cross the boundary
