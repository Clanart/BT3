# Q3737: NEAR bind_token refund callback interprets missing storage check as success

## Question
Can an unprivileged attacker use `refund callback after public `bind_token`` to make `near/omni-bridge/src/lib.rs::bind_token_refund` misread asynchronous storage-check results because of refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout.
