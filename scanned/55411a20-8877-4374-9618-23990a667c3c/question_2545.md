# Q2545: NEAR omitted storage-check result helper final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer callbacks after storage payment checks` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` violate `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account` in the `final settlement and later fee claim can diverge` attack class because interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
