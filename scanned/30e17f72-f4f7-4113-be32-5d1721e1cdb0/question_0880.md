# Q880: NEAR unlock_tokens_if_needed unlock or relock asymmetry via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public finalize paths` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `unlock or relock asymmetry` under unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
