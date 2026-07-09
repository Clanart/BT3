# Q1942: NEAR NativeFeeRestricted gating fee payout and storage refund overlap at boundary values

## Question
Can an unprivileged attacker trigger `public `ft_on_transfer` path for outbound transfers` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` violate `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions` in the `fee payout and storage refund overlap` attack class because permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
