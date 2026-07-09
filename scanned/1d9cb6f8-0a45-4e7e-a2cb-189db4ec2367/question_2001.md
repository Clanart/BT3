# Q2001: NEAR bind_token refund refund goes to wrong logical owner

## Question
Can an unprivileged attacker exploit callbacks behind `refund callback after public `bind_token`` so that `near/omni-bridge/src/lib.rs::bind_token_refund` refunds storage to an account other than the one that actually funded the state because of refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage.
