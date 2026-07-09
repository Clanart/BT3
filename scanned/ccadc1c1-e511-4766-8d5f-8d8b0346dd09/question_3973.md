# Q3973: NEAR sign_transfer callback captured predecessor identity can be abused for fee payout through cross-module drift

## Question
Can an unprivileged attacker use `MPC-signature callback reachable from public `sign_transfer`` with control over callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow and desynchronize `near/omni-bridge/src/lib.rs::sign_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `captured predecessor identity can be abused for fee payout` attack class because conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::sign_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
