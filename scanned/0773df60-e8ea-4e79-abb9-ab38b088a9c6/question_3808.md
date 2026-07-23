# Q3808: Replay context into transfer_to_btc_wallet

## Question
Can an unprivileged attacker use auth-bypass attempt into gRPC `ClementineOperator.TransferToBtcWallet` with attacker-controlled the `recovery_taproot_address` in `BaseDeposit` so `transfer_to_btc_wallet` reuses a previously accepted context, causing the deposit-to-move-tx binding to be consumed twice and breaking the invariant that signatures gathered for one deposit must never authorize a different deposit, replacement, or round context, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: core/src/rpc/operator.rs::transfer_to_btc_wallet
- Entrypoint: auth-bypass attempt into gRPC `ClementineOperator.TransferToBtcWallet`
- Attacker controls: the `recovery_taproot_address` in `BaseDeposit`
- Exploit idea: reuse or replay previously consumed the `recovery_taproot_address` in `BaseDeposit` in a fresh context
- Invariant to test: signatures gathered for one deposit must never authorize a different deposit, replacement, or round context
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: add a Rust integration test that runs the deposit pipeline twice with mutated attacker input and assert the move/emergency-stop signatures stay bound to one deposit context
