# Q1657: `from_scripts` may be driven twice for one funded deposit outpoint

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator drive `from_scripts` in `core/src/builder/transaction/input.rs` twice for the same `deposit_outpoint` - with a second `DepositData` that differs only in a field `DepositData::eq` ignores (cached `nofn_xonly_pk`, actor ordering, watchtower list) - so two distinct move txids are presigned for one 10 BTC deposit and the Citrea Bridge contract can be made to mint against both?

## Target
- File/function: `core/src/builder/transaction/input.rs` -> `from_scripts` (This module defines types and utilities for representing and handling transaction inputs used in [`super::TxHandler`])
- Entrypoint: aggregator `NewDeposit` (twice) -> `from_scripts`
- Attacker controls: every field of `DepositData`, and the ability to submit two aggregator rounds concurrently; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: obtain two N-of-N presigned move-to-vault transactions backed by a single funded output, then settle both
- Invariant to test: the number of distinct move txids the verifiers presign for one deposit outpoint == 1
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a duplicate/replayed withdrawal intent
- Fast validation: run two `new_deposit` rounds for one outpoint in a regtest test and assert the second is rejected
