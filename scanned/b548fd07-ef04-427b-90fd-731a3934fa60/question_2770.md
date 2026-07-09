# Q2770: NEAR unlock_tokens_if_needed global asset-conservation invariant break via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public finalize paths` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `global asset-conservation invariant break` under unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
