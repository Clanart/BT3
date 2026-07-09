# Q1518: NEAR bind_token refund storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `refund callback after public `bind_token`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::bind_token_refund` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
