# Q2240: NEAR fast-transfer storage encoding storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer claim and resolution flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
