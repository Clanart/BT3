# Q1728: send-underlying via transfer: normalize a real holding to zero USD while the paired debt

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls the destination principal, including the market, the market-vault or the treasury reach `send-underlying` (mainnet/contracts/vault/v0-vault-stx.clar:296) in a state where it normalize a real holding to zero USD while the paired debt normalizes upward? Given that it pushes the underlying under an `as-contract?` post-condition scope, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:296` -> `send-underlying`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the destination principal, including the market, the market-vault or the treasury
- Exploit idea: `send-underlying` pushes the underlying under an `as-contract?` post-condition scope. Reach it through `transfer` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the destination principal, including the market, the market-vault or the treasury across its boundary values through `transfer` in simnet and assert `send-underlying` never returns a value that breaks the invariant.
