# Q1532: NEAR required_balance_for_fin_transfer storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal accounting helper reached from public finalize paths` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under computes how much storage balance a finalized transfer consumes on Near, violating `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
