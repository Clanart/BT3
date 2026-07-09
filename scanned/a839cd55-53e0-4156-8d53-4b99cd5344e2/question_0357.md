# Q357: NEAR bind_token refund partial deployment rollback leaves live alias through cross-module drift

## Question
Can an unprivileged attacker use `refund callback after public `bind_token`` with control over callback success or failure, predecessor account chosen for refund, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::bind_token_refund` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `partial deployment rollback leaves live alias` attack class because refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token_refund` and the adjacent storage billing and refund bookkeeping after every branch.
