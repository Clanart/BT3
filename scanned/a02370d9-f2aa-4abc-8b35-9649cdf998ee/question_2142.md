# Q2142: NEAR resolve_fast_transfer fast path can pay before canonical parameters are locked via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after `send_tokens` in the fast Near path` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/lib.rs::resolve_fast_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path can pay before canonical parameters are locked` under burns tokens for deployed assets and removes the fast-transfer state only when the callback indicates a refund-like path, violating `the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_fast_transfer`
- Entrypoint: `callback after `send_tokens` in the fast Near path`
- Attacker controls: token id, fast-transfer id, `ft_transfer_call` refund behavior, and the sent amount
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the callback result must not let attackers keep recipient funds while also preserving fast-transfer state or avoiding the compensating burn
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
