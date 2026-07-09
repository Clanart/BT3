# Q3133: NEAR fast-transfer storage encoding numeric cast or overflow changes economic meaning at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer claim and resolution flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` violate `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored` in the `numeric cast or overflow changes economic meaning` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
