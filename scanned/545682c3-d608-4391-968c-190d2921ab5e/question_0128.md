# Q128: NEAR NativeFeeRestricted gating fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public `ft_on_transfer` path for outbound transfers` with crafted amount, fee, or native-fee inputs and make `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` use inconsistent fee and principal values across permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state, violating `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
