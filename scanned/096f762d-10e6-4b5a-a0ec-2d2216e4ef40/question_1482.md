# Q1482: NEAR provenance of `predecessor_account_id` storage-preparation omission changes settlement meaning

## Question
Can an unprivileged attacker make `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` omit or reorder required storage setup so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` settles under a different assumption about who can receive principal or fees because of captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned.
