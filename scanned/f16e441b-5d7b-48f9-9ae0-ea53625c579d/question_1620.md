# Q1620: NEAR NativeFeeRestricted gating fee payout and storage refund overlap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `ft_on_transfer` path for outbound transfers` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee payout and storage refund overlap` under permits or denies native-fee funding during outbound init depending on the signer’s `NativeFeeRestricted` role and storage-balance state, violating `role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer storage-owner logic`
- Entrypoint: `public `ft_on_transfer` path for outbound transfers`
- Attacker controls: signer role membership, native fee, storage balance, and resume timing
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: role-gated native-fee rules must not let an unprivileged caller smuggle native-fee liabilities into another storage owner or bypass intended restrictions
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
