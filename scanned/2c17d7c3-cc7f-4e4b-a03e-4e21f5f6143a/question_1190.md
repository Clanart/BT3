# Q1190: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
rewards/BNBZapper.sol: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Under WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, is there an unprivileged sequence of `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` that leaves `routePairAddresses[token]` unreconciled with `the path built by _findRouteToBnb`, violates the invariant that value left over from a swap must be returned to the account that supplied it, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `routePairAddresses[token]` must stay reconciled with `the path built by _findRouteToBnb`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up WombatBribeManager.previewBnbAmountForHarvest is being read by an integrator in the same block, snapshot `routePairAddresses[token]` and `the path built by _findRouteToBnb`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
