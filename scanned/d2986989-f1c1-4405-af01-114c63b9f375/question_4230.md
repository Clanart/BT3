# Q4230: mask-update via collateral-remove: make a required price path abort so the position can no lo

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling whether the position has any enabled debt row (the has-debt branch), can an unprivileged attacker make `mask-update` (mainnet/contracts/market/v0-market-vault.clar:94) make a required price path abort so the position can no longer be closed or seized? `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero, so the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:94` -> `mask-update`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: whether the position has any enabled debt row (the has-debt branch)
- Exploit idea: `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero. Reach it through `collateral-remove` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz whether the position has any enabled debt row (the has-debt branch) across its boundary values through `collateral-remove` in simnet and assert `mask-update` never returns a value that breaks the invariant.
