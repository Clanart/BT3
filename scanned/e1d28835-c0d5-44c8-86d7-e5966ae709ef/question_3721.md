# Q3721: NEAR sign_transfer callback captured predecessor identity can be abused for fee payout

## Question
Can an unprivileged attacker exploit asynchronous callbacks behind `MPC-signature callback reachable from public `sign_transfer`` so that `near/omni-bridge/src/lib.rs::sign_transfer_callback` trusts the wrong predecessor account for fee payout or storage charging, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject.
