# Q0738: mask-update via collateral-remove-redeem: normalize a real holding to zero USD while the paired debt

## Question
Entering through `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) while controlling `receiver` for the underlying leg, can an unprivileged attacker make `mask-update` (mainnet/contracts/market/v0-market-vault.clar:94) normalize a real holding to zero USD while the paired debt normalizes upward? `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero, so the invariant that collateral is valued low and debt is valued high at every call site without exception would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:94` -> `mask-update`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `receiver` for the underlying leg
- Exploit idea: `mask-update` sets or clears one bit, clearing only when the row reaches exactly zero. Reach it through `collateral-remove-redeem` and normalize a real holding to zero USD while the paired debt normalizes upward.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `receiver` for the underlying leg across its boundary values through `collateral-remove-redeem` in simnet and assert `mask-update` never returns a value that breaks the invariant.
