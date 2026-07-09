# Q5: NEAR sign_transfer callback origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `MPC-signature callback reachable from public `sign_transfer`` with control over callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow and make `near/omni-bridge/src/lib.rs::sign_transfer_callback` advance or reuse bridge nonces inconsistently with conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
