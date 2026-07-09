# Q2544: NEAR fast-transfer storage encoding storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer claim and resolution flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` violate `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored` in the `storage withdrawal escapes live liabilities` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
