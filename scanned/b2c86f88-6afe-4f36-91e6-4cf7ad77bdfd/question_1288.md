# Q1288: NEAR omitted storage-check result helper delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `public finalize and fast-transfer callbacks after storage payment checks` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` violate `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account` in the `delivery callback leaves inconsistent state` attack class because interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
