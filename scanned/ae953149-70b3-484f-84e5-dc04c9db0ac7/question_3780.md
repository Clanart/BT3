# Q3780: `get_replacement_deposit_move_txids` verifies a withdrawal signature under a caller-chosen sighash type

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction pass a `taproot::Signature` whose `sighash_type` is not `SinglePlusAnyoneCanPay` into the path that reaches `get_replacement_deposit_move_txids` in `core/src/citrea.rs`, given that the sighash is computed with `in_signature.sighash_type` supplied by that same caller, so the signature the bridge validates commits to a different set of inputs/outputs than the transaction that ultimately settles the withdrawal?

## Target
- File/function: `core/src/citrea.rs` -> `get_replacement_deposit_move_txids`
- Entrypoint: aggregator `Withdraw` / `OptimisticPayout` -> `get_replacement_deposit_move_txids`
- Attacker controls: the sighash flag byte, the withdrawal UTXO, the output script and amount; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: have the bridge accept a signature whose commitment does not cover the payout it settles, then reuse or reshape that transaction
- Invariant to test: the sighash type verified against the withdrawer's key == a flag committing the exact output the bridge settles against
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: call the withdrawal path with each sighash flag in a regtest test and assert only SinglePlusAnyoneCanPay is accepted
