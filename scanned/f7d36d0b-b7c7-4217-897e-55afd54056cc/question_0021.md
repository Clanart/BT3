# Q21: NEAR bind_token refund partial deployment rollback leaves live alias

## Question
Can an unprivileged attacker trigger a partial failure through `refund callback after public `bind_token`` such that `near/omni-bridge/src/lib.rs::bind_token_refund` leaves behind either a live token without mappings or mappings without a usable token because of refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound.
