# Q2854: NEAR wNEAR unwrap path callback-bearing token flow exposes inconsistent intermediate state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback-bearing token flow exposes inconsistent intermediate state` under unwraps wNEAR with one yocto deposit and then forwards raw NEAR to the recipient in a callback, violating `wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens wNEAR branch`
- Entrypoint: `public finalize and fast-transfer payouts when the token is `wnear_account_id` and `msg` is empty`
- Attacker controls: token id chosen through mapping, recipient account, amount, and callback success/failure
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: wNEAR unwrapping must not let attackers trigger a payout path that spends wrapped liquidity while leaving the associated transfer finalization unsettled or replayable
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
