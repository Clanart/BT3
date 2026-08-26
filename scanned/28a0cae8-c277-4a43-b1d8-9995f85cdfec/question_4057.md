# Q4057: BaseRewardPool.getReward - _sendReward zeroes userRewards before the transfer settles

## Question
rewards/BaseRewardPool.sol - _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Can an unprivileged attacker controlling the timing of the claim, reachable through MasterMagpie.multiclaim, under the attacker funds the action with a flash loan of the staking token repaid in the same transaction, exploit this through `getReward(address _account, address _receiver)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and the invariant that a reward entitlement may only be cleared once the exact amount has been irrevocably delivered, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPool.sol -> `getReward(address _account, address _receiver)` (mechanism: _sendReward zeroes userRewards before the transfer settles)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the timing of the claim, reachable through MasterMagpie.multiclaim
- Exploit idea: _sendReward() writes userRewards[token][account] = 0 and then calls safeTransfer, so a reward token that consumes less than the full amount, reverts silently, or re-enters leaves the accounting cleared with the value not delivered. Precondition: the attacker funds the action with a flash loan of the staking token repaid in the same transaction.
- Invariant to test: a reward entitlement may only be cleared once the exact amount has been irrevocably delivered; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the action with a flash loan of the staking token repaid in the same transaction, have the attacker run `getReward(address _account, address _receiver)`, then assert the victim's claimable value and the `balanceOf(account)` versus `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` relation are unchanged by the attacker's transaction.
