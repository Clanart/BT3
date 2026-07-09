# Q790: NEAR omitted storage-check result helper delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `public finalize and fast-transfer callbacks after storage payment checks` that causes `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` to keep or remove settlement state inconsistently with delivered value because of interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists, violating `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
