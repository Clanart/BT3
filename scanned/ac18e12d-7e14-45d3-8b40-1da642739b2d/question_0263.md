# Q0263: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
In rewards/BNBZapper.sol, zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while routePairAddresses is unset for the token so a direct two-hop path is used, and drive `previewAmount(token, amount)` out of agreement with `the executed swap output` - breaking the invariant that value left over from a swap must be returned to the account that supplied it - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: routePairAddresses is unset for the token so a direct two-hop path is used.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted) under routePairAddresses is unset for the token so a direct two-hop path is used, asserting on every row that value left over from a swap must be returned to the account that supplied it.
