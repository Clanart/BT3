# Q4470: `get_deposit_sig_owner` may be driven twice for one funded deposit outpoint

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator drive `get_deposit_sig_owner` in `core/src/builder/transaction/deposit_signature_owner.rs` twice for the same `deposit_outpoint` - with a second `DepositData` that differs only in a field `DepositData::eq` ignores (cached `nofn_xonly_pk`, actor ordering, watchtower list) - so two distinct move txids are presigned for one 10 BTC deposit and the Citrea Bridge contract can be made to mint against both?

## Target
- File/function: `core/src/builder/transaction/deposit_signature_owner.rs` -> `get_deposit_sig_owner` (This module provides types and logic for mapping transaction signature requirements to protocol entities in the Clementine bridge)
- Entrypoint: aggregator `NewDeposit` (twice) -> `get_deposit_sig_owner`
- Attacker controls: every field of `DepositData`, and the ability to submit two aggregator rounds concurrently; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: obtain two N-of-N presigned move-to-vault transactions backed by a single funded output, then settle both
- Invariant to test: the number of distinct move txids the verifiers presign for one deposit outpoint == 1
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run two `new_deposit` rounds for one outpoint in a regtest test and assert the second is rejected
