# Q0635: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
rewards/BNBZapper.sol - zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Can an unprivileged attacker controlling fromToken, amount, minRec and receiver, all unrestricted, under the caller sets minRec to zero and sandwiches the PancakeSwap pair, exploit this through `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` to break the reconciliation between `minRec supplied by the caller` and `amounts[amounts.length - 1] returned by the router` and the invariant that value left over from a swap must be returned to the account that supplied it, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: the caller sets minRec to zero and sandwiches the PancakeSwap pair.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`: constrain the setup so that the caller sets minRec to zero and sandwiches the PancakeSwap pair, fuzz the attacker inputs (fromToken, amount, minRec and receiver, all unrestricted), and assert after every call that value left over from a swap must be returned to the account that supplied it.
