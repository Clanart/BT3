# Q1007: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
In rewards/BNBZapper.sol, zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Can an unprivileged attacker reach this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` while a residual balance of the token from an earlier zap sits on the contract, and drive `previewAmount(token, amount)` out of agreement with `the executed swap output` - breaking the invariant that value left over from a swap must be returned to the account that supplied it - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: a residual balance of the token from an earlier zap sits on the contract.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `previewAmount(token, amount)` must stay reconciled with `the executed swap output`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a residual balance of the token from an earlier zap sits on the contract, have the attacker run `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, then assert the victim's claimable value and the `previewAmount(token, amount)` versus `the executed swap output` relation are unchanged by the attacker's transaction.
