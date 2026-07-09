# Q1679: NEAR bind_token refund storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `refund callback after public `bind_token`` with control over callback success or failure, predecessor account chosen for refund, and attached deposit and desynchronize `near/omni-bridge/src/lib.rs::bind_token_refund` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token_refund` and the adjacent storage billing and refund bookkeeping after every branch.
