# Q5688: `to_secp_sk` may accept a self-funded replacement deposit for a fabricated `old_move_txid`

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator reach `to_secp_sk` in `core/src/musig2.rs` with `DepositType::ReplacementDeposit` whose `old_move_txid` names a move tx that was never created or never spent by the `Multisig::from_security_council` path, given that `Verifier::is_deposit_valid` never cross-checks `old_move_txid` against a real prior vault, and thereby obtain an N-of-N presigned vault the bridge accounting treats as a replacement for value it never held?

## Target
- File/function: `core/src/musig2.rs` -> `to_secp_sk` (Helper functions for the MuSig2 signature scheme)
- Entrypoint: aggregator `NewDeposit` with a `ReplacementDeposit` type -> `to_secp_sk`
- Attacker controls: `old_move_txid`, the funded replacement output, and the whole `DepositParams` payload; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: mint or re-mint against a replacement deposit that replaces nothing
- Invariant to test: an accepted `ReplacementDeposit` `old_move_txid` == the txid of a move-to-vault that existed and was spent by the security council path
- Expected Immunefi impact: Critical - direct theft of bridged BTC/cBTC via a deposit/withdraw verification bug
- Fast validation: submit a replacement deposit with a random `old_move_txid` in a mocked-Citrea test and assert rejection
