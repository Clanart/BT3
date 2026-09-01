# Q3718: `get_deposit_outpoint` and the aggregator-supplied verifier set that derives `nofn_xonly_pk`

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator influence the verifier/watchtower/operator lists carried in `DepositParams` so that `get_deposit_outpoint` in `core/src/deposit.rs` derives, caches, or compares an `nofn_xonly_pk` (via `AggregateFromPublicKeys::from_musig2_pks`, `get_verifiers`, `get_watchtowers`) that differs from the key actually controlling the funded output, producing a vault whose N-of-N key nobody can reconstruct?

## Target
- File/function: `core/src/deposit.rs` -> `get_deposit_outpoint` (This module defines the data structures related to Citrea deposits in the Clementine bridge)
- Entrypoint: aggregator `NewDeposit` actor lists -> `DepositData::get_nofn_xonly_pk` -> `get_deposit_outpoint`
- Attacker controls: the ordering, duplication and membership of the actor public-key lists in `DepositParams`; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: cause the presigned vault key to diverge from the key the deposit output pays
- Invariant to test: the `nofn_xonly_pk` used to build the vault output == the key aggregated from exactly the verifiers that signed
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: unit-test `get_nofn_xonly_pk` under permuted/duplicated verifier lists and assert a single canonical key
