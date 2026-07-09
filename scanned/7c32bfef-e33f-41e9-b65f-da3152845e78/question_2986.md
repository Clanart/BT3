# Q2986: NEAR fast-transfer storage encoding numeric cast or overflow changes economic meaning through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer claim and resolution flows` with control over fast-transfer id fields, relayer id, finalised flag, and storage owner and desynchronize `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `numeric cast or overflow changes economic meaning` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` and the adjacent storage billing and refund bookkeeping after every branch.
