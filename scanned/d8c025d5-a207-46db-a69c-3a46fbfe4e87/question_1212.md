# Q1212: NEAR unlock_tokens_if_needed unlock or relock asymmetry at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public finalize paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` violate `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more` in the `unlock or relock asymmetry` attack class because unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
