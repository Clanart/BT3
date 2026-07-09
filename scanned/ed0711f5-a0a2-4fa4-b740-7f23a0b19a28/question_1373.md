# Q1373: NEAR get_locked_tokens global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public lock-accounting view used by bridge operators and relayers` with the code paths summarized by `near/omni-bridge/src/token_lock.rs::get_locked_tokens` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
