# Q712: NEAR get_locked_tokens locked balance diverges from actual locked asset

## Question
Can an unprivileged attacker use `public lock-accounting view used by bridge operators and relayers` so that `near/omni-bridge/src/token_lock.rs::get_locked_tokens` changes the `locked_tokens` table without an equal change in actual bridge custody, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity.
