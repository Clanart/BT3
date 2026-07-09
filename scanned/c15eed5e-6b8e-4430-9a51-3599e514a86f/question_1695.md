# Q1695: NEAR get_locked_tokens global asset-conservation invariant break through cross-module drift

## Question
Can an unprivileged attacker use `public lock-accounting view used by bridge operators and relayers` with control over chain kind and token id chosen by the caller and desynchronize `near/omni-bridge/src/token_lock.rs::get_locked_tokens` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `global asset-conservation invariant break` attack class because reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::get_locked_tokens` and the adjacent lock and unlock accounting after every branch.
