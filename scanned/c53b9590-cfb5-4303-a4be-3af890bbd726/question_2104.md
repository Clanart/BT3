# Q2104: NEAR wNEAR unwrap path custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` to make `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` increase wrapped supply or reduce custody without the complementary change on the other side, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
