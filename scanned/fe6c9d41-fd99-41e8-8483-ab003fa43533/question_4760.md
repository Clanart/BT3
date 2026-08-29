# Q4760: add-user-collateral via transfer: apply a transform after the gate that was supposed to boun

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls the timing relative to a pledge or a liquidation reach `add-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:198) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it adds to the collateral row with a graceful u0 default, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:198` -> `add-user-collateral`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the timing relative to a pledge or a liquidation
- Exploit idea: `add-user-collateral` adds to the collateral row with a graceful u0 default. Reach it through `transfer` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with the timing relative to a pledge or a liquidation varied, and assert that the value `add-user-collateral` returns is identical in both runs; a divergence confirms the finding.
