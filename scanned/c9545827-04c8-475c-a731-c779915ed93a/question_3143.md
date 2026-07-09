# Q3143: NEAR NativeFeeRestricted gating storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public `ft_on_transfer` path for outbound transfers` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` violate `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions` in the `storage withdrawal escapes live liabilities` attack class because permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
