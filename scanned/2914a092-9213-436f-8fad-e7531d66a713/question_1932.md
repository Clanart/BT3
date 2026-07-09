# Q1932: NEAR fast-transfer storage encoding storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer claim and resolution flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` violate `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored` in the `storage quote underestimates live state` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
