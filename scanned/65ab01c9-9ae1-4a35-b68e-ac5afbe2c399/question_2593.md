# Q2593: NEAR sign_transfer callback callback refund creates value gap

## Question
Can an unprivileged attacker cause the callback resolution behind `MPC-signature callback reachable from public `sign_transfer`` to refund tokens, keep a pending claim, or skip a compensating burn in `near/omni-bridge/src/lib.rs::sign_transfer_callback` because of conditionally removes the stored transfer when fee is zero and emits the signed event after MPC returns a signature, violating `signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::sign_transfer_callback`
- Entrypoint: `MPC-signature callback reachable from public `sign_transfer``
- Attacker controls: callback timing, whether fee is zero, stored transfer contents, and replay of the same public sign flow
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned.
- Invariant to test: signature callbacks must not let stored transfers disappear, survive, or be re-signed in a way that breaks one-deposit-one-signed-message semantics
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer.
