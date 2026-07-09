# Q3284: NEAR wNEAR unwrap path different callback outcomes produce the same user-visible success

## Question
Can an unprivileged attacker use `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` so that `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` treats materially different callback outcomes as the same economic result because of unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition.
