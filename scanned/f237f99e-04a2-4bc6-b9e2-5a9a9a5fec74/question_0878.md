# Q878: NEAR get_locked_tokens locked balance diverges from actual locked asset via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public lock-accounting view used by bridge operators and relayers` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/token_lock.rs::get_locked_tokens` ends up accepting two inconsistent interpretations of the same economic event specifically around `locked balance diverges from actual locked asset` under reads the `locked_tokens` table that tracks bridge liquidity locked on behalf of foreign-chain claims, violating `every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::get_locked_tokens`
- Entrypoint: `public lock-accounting view used by bridge operators and relayers`
- Attacker controls: chain kind and token id chosen by the caller
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: every observed lock amount must stay synchronized with actual bridge custody and pending outbound obligations
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
