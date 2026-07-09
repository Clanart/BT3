# Q2472: NEAR unlock_tokens_if_needed locked balance diverges from actual locked asset at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public finalize paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` violate `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more` in the `locked balance diverges from actual locked asset` attack class because unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
