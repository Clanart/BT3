# Q3050: NEAR bind_token refund unregister can sever state that callbacks still need at boundary values

## Question
Can an unprivileged attacker trigger `refund callback after public `bind_token`` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::bind_token_refund` violate `refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering` in the `unregister can sever state that callbacks still need` attack class because refunds either the explicit callback amount or the whole attached deposit after token-binding attempts becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_refund`
- Entrypoint: `refund callback after public `bind_token``
- Attacker controls: callback success or failure, predecessor account chosen for refund, and attached deposit
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: refund logic must never let an attacker keep a successful binding while reclaiming the full deposit, or lose funds on a successful bind due to callback ordering
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
