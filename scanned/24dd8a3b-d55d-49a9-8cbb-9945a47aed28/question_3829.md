# Q3829: NEAR provenance of `predecessor_account_id` captured predecessor identity can be abused for fee payout

## Question
Can an unprivileged attacker exploit asynchronous callbacks behind `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` trusts the wrong predecessor account for fee payout or storage charging, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject.
