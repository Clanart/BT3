# Q2623: NEAR unlock_tokens_if_needed global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `internal helper reached from public finalize paths` with the code paths summarized by `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
