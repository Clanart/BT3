# Q1663: NEAR sign_transfer callback resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `MPC-signature callback reachable from public `sign_transfer`` with control over callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow and desynchronize `near/omni-bridge/src/lib.rs::sign_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::sign_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
