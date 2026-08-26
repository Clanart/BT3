# Q1219: BNBZapper.zapInToken - route path derived from mutable owner state without validation

## Question
In rewards/BNBZapper.sol, _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Does `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` let an unprivileged caller exploit that under WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, so that `minRec supplied by the caller` diverges from `amounts[amounts.length - 1] returned by the router`, the invariant that a routing table entry must be validated against real liquidity before value is sent through it is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: route path derived from mutable owner state without validation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: _findRouteToBnb() builds a two or three hop path purely from routePairAddresses[token] with no check that the resulting pair exists or holds liquidity, so a route through an empty pair executes at an arbitrary price. Precondition: WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block.
- Invariant to test: a routing table entry must be validated against real liquidity before value is sent through it; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`: constrain the setup so that WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, fuzz the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted), and assert after every call that a routing table entry must be validated against real liquidity before value is sent through it.
