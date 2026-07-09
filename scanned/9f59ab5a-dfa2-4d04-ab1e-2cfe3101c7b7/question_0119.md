# Q119: NEAR omitted storage-check result helper recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public finalize and fast-transfer callbacks after storage payment checks` that makes `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers` settle principal to one party but authorize fee claim or callback routing for another due to interprets callback results from storage-balance checks to decide whether recipient and fee-recipient storage exists, violating `callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::check_storage_balance_result and callback consumers`
- Entrypoint: `public finalize and fast-transfer callbacks after storage payment checks`
- Attacker controls: storage action count, callback result size, and chosen index for recipient/fee-recipient checks
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: callback-result interpretation must not let a missing or reordered storage check masquerade as success and push settlement into an underprepared recipient account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
