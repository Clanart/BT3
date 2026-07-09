# Q799: NEAR NativeFeeRestricted gating storage payer or owner spoofing

## Question
Can an unprivileged attacker cause `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` to bill, refund, or resume the wrong storage owner through `public `ft_on_transfer` path for outbound transfers` by abusing permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state, violating `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer.
