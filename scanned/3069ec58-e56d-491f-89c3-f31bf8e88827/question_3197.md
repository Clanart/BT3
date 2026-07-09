# Q3197: NEAR bind_token refund promise bookkeeping can be overwritten or orphaned

## Question
Can an unprivileged attacker use `refund callback after public `bind_token`` to overwrite or strand another deferred operation inside `near/omni-bridge/src/lib.rs::bind_token_refund` because of refunds either the explicit callback amount or the whole attached deposit after token-binding attempts, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Probe maps keyed only by account ids or derived storage accounts when multiple pending operations are possible.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Create overlapping deferred operations and assert that each one has independent bookkeeping and cleanup.
