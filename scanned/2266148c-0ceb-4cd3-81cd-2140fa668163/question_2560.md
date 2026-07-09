# Q2560: NEAR wNEAR unwrap path custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` violate `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable` in the `custody accounting diverges from wrapped supply` attack class because unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
