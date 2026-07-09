# Q2016: NEAR unlock_tokens_if_needed locked balance diverges from actual locked asset

## Question
Can an unprivileged attacker use `internal helper reached from public finalize paths` so that `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` changes the `locked_tokens` table without an equal change in actual bridge custody, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity.
