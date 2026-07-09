# Q2250: NEAR NativeFeeRestricted gating storage quote underestimates live state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `ft_on_transfer` path for outbound transfers` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage quote underestimates live state` under permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state, violating `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
