# Q2609: NEAR bind_token refund unregister can sever state that callbacks still need

## Question
Can an unprivileged attacker combine `refund callback after public `bind_token`` with later callbacks so that `near/omni-bridge/src/lib.rs::bind_token_refund` unregisters storage ownership before asynchronous cleanup runs, violating `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely.
