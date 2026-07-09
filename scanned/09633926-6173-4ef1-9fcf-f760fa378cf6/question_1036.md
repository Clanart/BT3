# Q1036: NEAR storage_deposit storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over account id to credit, attached deposit, and timing around pending transfers or yields and desynchronize `near/omni-bridge/src/storage.rs::storage_deposit` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because tracks storage balances per account and is used by bridge paths that bill users and relayers for pending transfer records, violating `storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_deposit`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: account id to credit, attached deposit, and timing around pending transfers or yields
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_deposit` and the adjacent storage billing and refund bookkeeping after every branch.
