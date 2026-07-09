# Q2740: NEAR sign_transfer callback callback refund creates value gap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `MPC-signature callback reachable from public `sign_transfer`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::sign_transfer_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback refund creates value gap` under conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
