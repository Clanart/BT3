# Q1100: debt-preview via transfer: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls the timing relative to a pledge or a liquidation reach `debt-preview` (mainnet/contracts/vault/v0-vault-stx.clar:331) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it computes cumulative debt from `principal-scaled` and the FORWARD index, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:331` -> `debt-preview`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the timing relative to a pledge or a liquidation
- Exploit idea: `debt-preview` computes cumulative debt from `principal-scaled` and the FORWARD index. Reach it through `transfer` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `transfer` twice with the timing relative to a pledge or a liquidation varied, and assert that the value `debt-preview` returns is identical in both runs; a divergence confirms the finding.
