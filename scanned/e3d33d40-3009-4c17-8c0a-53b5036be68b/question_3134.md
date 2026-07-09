# Q3134: NEAR omitted storage-check result helper fee recipient can be substituted or reclaimed by attacker at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer callbacks after storage payment checks` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` violate `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account` in the `fee recipient can be substituted or reclaimed by attacker` attack class because interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
