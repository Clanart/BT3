# Q3210: NEAR lock_tokens_if_needed burn debits the wrong logical account

## Question
Can an unprivileged attacker use `internal helper reached from public init/finalize/fast paths` so that `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` burns or withholds value from a caller context different from the one the bridge event later attributes, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event.
