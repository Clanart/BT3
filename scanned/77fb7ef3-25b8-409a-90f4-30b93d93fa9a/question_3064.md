# Q3064: NEAR unlock_tokens_if_needed global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public finalize paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` violate `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more` in the `global asset-conservation invariant break` attack class because unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
