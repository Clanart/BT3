# Q3316: `get_deposit_sig_owner` and the user recovery timelock can both be live at once

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `get_deposit_sig_owner` in `core/src/builder/transaction/deposit_signature_owner.rs` with a `recovery_taproot_address` and a `user_takes_after` relative timelock such that the user's recovery path in `TimelockScript` becomes spendable while the presigned move-to-vault for the same outpoint is still pending, letting the depositor reclaim the deposit after cBTC was minted for it?

## Target
- File/function: `core/src/builder/transaction/deposit_signature_owner.rs` -> `get_deposit_sig_owner` (This module provides types and logic for mapping transaction signature requirements to protocol entities in the Clementine bridge)
- Entrypoint: aggregator `NewDeposit` -> `DepositData::get_deposit_scripts` -> `get_deposit_sig_owner`
- Attacker controls: `recovery_taproot_address`, the deposit confirmation height, and the timing of the aggregator round; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: race the recovery timelock against move-to-vault confirmation so the same BTC backs both a mint and a refund
- Invariant to test: a deposit whose move-to-vault is presigned is never spendable by the depositor's recovery path afterwards
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: regtest: fund a deposit, mine `user_takes_after` blocks before broadcasting move-to-vault, assert the recovery spend fails
