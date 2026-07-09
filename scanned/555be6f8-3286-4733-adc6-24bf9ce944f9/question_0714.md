# Q714: NEAR unlock_tokens_if_needed unlock or relock asymmetry

## Question
Can an unprivileged attacker make `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` unlock, relock, or revert lock state inconsistently during `internal helper reached from public finalize paths` because of unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path.
