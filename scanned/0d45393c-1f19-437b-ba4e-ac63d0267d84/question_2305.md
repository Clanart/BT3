# Q2305: NEAR bind_token refund refund goes to wrong logical owner through cross-module drift

## Question
Can an unprivileged attacker use `refund callback after public `bind_token`` with control over callback success or failure, predecessor account chosen for refund, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::bind_token_refund` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `refund goes to wrong logical owner` attack class because refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token_refund` and the adjacent storage billing and refund bookkeeping after every branch.
