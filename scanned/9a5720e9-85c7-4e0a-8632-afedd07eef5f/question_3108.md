# Q3108: `create_ready_to_reimburse_txhandler` and the allowed payout destination

## Question
Can an unprivileged user who burns cBTC via `withdraw` on the Citrea Bridge contract and registers a withdrawal UTXO of their own construction pass an `output_script_pubkey` that passes the `is_p2tr/is_p2pkh/is_p2sh/is_p2wpkh/is_p2wsh` filter reached from `create_ready_to_reimburse_txhandler` in `core/src/builder/transaction/operator_collateral.rs` yet is unspendable (an unknown-witness-version wrapped form, an OP_RETURN-shaped p2sh, a provably-unspendable key), so a vault spend lands somewhere no one can ever redeem?

## Target
- File/function: `core/src/builder/transaction/operator_collateral.rs` -> `create_ready_to_reimburse_txhandler` (This module contains the logic for creating the `round_tx`, `ready_to_reimburse_tx`,)
- Entrypoint: aggregator `OptimisticPayout` -> `create_ready_to_reimburse_txhandler`
- Attacker controls: the exact `output_script_pubkey` bytes; attacker is an unprivileged withdrawer (burns cBTC on Citrea, registers a withdrawal UTXO, signs it, holds no protocol role or key)
- Exploit idea: burn a vault's contents into an unspendable output while the deposit is marked settled
- Invariant to test: the destination of a vault spend is redeemable by the withdrawer that requested it
- Expected Immunefi impact: Critical - permanent freezing of bridged funds (a move-to-vault UTXO that can never be spent)
- Fast validation: submit each degenerate script class to the payout path and assert rejection
