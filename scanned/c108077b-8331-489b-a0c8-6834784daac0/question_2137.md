# Q2137: NEAR sign_transfer callback stored state versus signed bytes mismatch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `MPC-signature callback reachable from public `sign_transfer`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::sign_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `stored state versus signed bytes mismatch` under conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Look for canonical-state versus emitted-bytes drift on optional strings, decimals, origin ids, or fee recipients. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Compare persisted transfer records to their signed or published payloads and assert byte-for-byte equivalence of all economically-relevant fields. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
