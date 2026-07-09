# Q3298: NEAR provenance of `predecessor_account_id` fee payout and storage refund overlap

## Question
Can an unprivileged attacker exploit `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` both refunds reserved storage and pays a fee out of the same economic event because of captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker.
