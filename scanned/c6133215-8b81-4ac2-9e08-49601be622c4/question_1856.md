# Q1856: NEAR get_locked_tokens global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `public lock-accounting view used by bridge operators and relayers` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::get_locked_tokens` violate `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations` in the `global asset-conservation invariant break` attack class because reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
