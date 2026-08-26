# Q0449: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
Consider rewards/BNBZapper.sol, where zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Assuming routePairAddresses points at a pair with no meaningful liquidity, can an unprivileged attacker turn this into a divergence between `routePairAddresses[token]` and `the path built by _findRouteToBnb` via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, breaking the invariant that value left over from a swap must be returned to the account that supplied it and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: routePairAddresses points at a pair with no meaningful liquidity.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence atomically under routePairAddresses points at a pair with no meaningful liquidity, asserting at the end that `routePairAddresses[token]` still equals `the path built by _findRouteToBnb` and the PoC's balance delta is non-positive.
