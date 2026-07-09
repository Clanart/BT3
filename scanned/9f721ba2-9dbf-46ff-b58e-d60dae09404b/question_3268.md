# Q3268: NEAR per-chain token origin detection locked balance diverges from actual locked asset

## Question
Can an unprivileged attacker use `public init/finalize/lock flows through token-origin checks` so that `near/omni-bridge/src/lib.rs::get_token_origin_chain` changes the `locked_tokens` table without an equal change in actual bridge custody, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Target lock/unlock helpers around failed callbacks, cross-chain forwarding, and fast transfers.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track real token/ETH/SOL custody alongside lock rows and assert exact equality of outstanding obligations and locked liquidity.
