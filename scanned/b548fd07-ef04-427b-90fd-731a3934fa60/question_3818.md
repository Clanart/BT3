# Q3818: NEAR wNEAR unwrap path cleanup order around callbacks reopens or strands value

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` so that `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` cleans up transfer or fast-transfer state in an order that either reopens replay or strands user funds after callback failure, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Focus on removal of pending records, finalization flags, and lock rollback relative to payout callbacks.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Inject failures at each callback boundary and assert that cleanup always leaves one consistent recoverable state.
