# Q3316: NEAR sign_transfer callback same fee collectible twice via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `MPC-signature callback reachable from public `sign_transfer`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::sign_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `same fee collectible twice` under conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
