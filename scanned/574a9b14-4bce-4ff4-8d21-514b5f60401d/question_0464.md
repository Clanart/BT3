# Q464: NEAR NativeFeeRestricted gating fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public `ft_on_transfer` path for outbound transfers` with control over signer role membership, native fee, storage balance, and resume timing and desynchronize `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state, violating `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` and the adjacent storage billing and refund bookkeeping after every branch.
