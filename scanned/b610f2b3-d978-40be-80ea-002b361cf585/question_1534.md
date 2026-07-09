# Q1534: NEAR get_locked_tokens global asset-conservation invariant break via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public lock-accounting view used by bridge operators and relayers` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/token_lock.rs::get_locked_tokens` ends up accepting two inconsistent interpretations of the same economic event specifically around `global asset-conservation invariant break` under reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
