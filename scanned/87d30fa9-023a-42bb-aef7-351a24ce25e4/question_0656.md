# Q656: NEAR provenance of `predecessor_account_id` recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` violate `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account` in the `recipient or fee-recipient rebinding` attack class because captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
