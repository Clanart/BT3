# Q341: NEAR sign_transfer callback origin and destination nonce desynchronization through cross-module drift

## Question
Can an unprivileged attacker use `MPC-signature callback reachable from public `sign_transfer`` with control over callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow and desynchronize `near/omni-bridge/src/lib.rs::sign_transfer_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `origin and destination nonce desynchronization` attack class because conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::sign_transfer_callback` and the adjacent replay-protection bookkeeping after every branch.
