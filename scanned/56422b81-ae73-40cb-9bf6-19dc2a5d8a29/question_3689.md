# Q3689: NEAR wNEAR unwrap path different callback outcomes produce the same user-visible success at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` violate `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable` in the `different callback outcomes produce the same user-visible success` attack class because unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
