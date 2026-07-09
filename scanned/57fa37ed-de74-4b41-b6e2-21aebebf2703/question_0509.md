# Q509: NEAR sign_transfer callback origin and destination nonce desynchronization at boundary values

## Question
Can an unprivileged attacker trigger `MPC-signature callback reachable from public `sign_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::sign_transfer_callback` violate `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics` in the `origin and destination nonce desynchronization` attack class because conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
