# Q822: NEAR provenance of `predecessor_account_id` delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` that causes `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` to keep or remove settlement state inconsistently with delivered value because of captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
