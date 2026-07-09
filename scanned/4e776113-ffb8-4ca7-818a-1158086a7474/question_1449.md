# Q1449: NEAR fast-transfer storage encoding storage quote underestimates live state

## Question
Can an unprivileged attacker reach `public fast-transfer claim and resolution flows` and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` reserve less storage than the live bridge state actually consumes because of deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
