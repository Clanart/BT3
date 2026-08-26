# Q1571: DelegateVoteRewardPool.getReward - _getDelegateReward zeroes the entitlement before the transfer

## Question
In rewards/DelegateVoteRewardPool.sol, _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Can an unprivileged attacker reach this through `getReward(address _for)` while protocolFee is non-zero and feeCollector is set, and drive `_balances[account]` out of agreement with `totalSupply` - breaking the invariant that an entitlement may only be cleared once the exact amount has been delivered - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: _getDelegateReward zeroes the entitlement before the transfer)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: _getDelegateReward() sets userRewards[token][account] = 0 and then calls safeTransfer, so a token that reverts or under-delivers leaves the entitlement cleared with the value undelivered. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: an entitlement may only be cleared once the exact amount has been delivered; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _for)` sequence atomically under protocolFee is non-zero and feeCollector is set, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
