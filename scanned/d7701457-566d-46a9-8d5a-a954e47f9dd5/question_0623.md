# Q623: NEAR omitted storage-check result helper recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer callbacks after storage payment checks` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` violate `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account` in the `recipient or fee-recipient rebinding` attack class because interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
