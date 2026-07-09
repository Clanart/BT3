# Q173: NEAR sign_transfer callback origin and destination nonce desynchronization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `MPC-signature callback reachable from public `sign_transfer`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::sign_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `origin and destination nonce desynchronization` under conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
