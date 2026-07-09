# Q1044: NEAR get_locked_tokens locked balance diverges from actual locked asset through cross-module drift

## Question
Can an unprivileged attacker use `public lock-accounting view used by bridge operators and relayers` with control over chain kind and token id chosen by the caller and desynchronize `near/omni-bridge/src/token_lock.rs::get_locked_tokens` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `locked balance diverges from actual locked asset` attack class because reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::get_locked_tokens` and the adjacent lock and unlock accounting after every branch.
