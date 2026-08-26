# Q4325: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/vlMGPBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an unprivileged attacker reach this through `updateFor(address _account)` while the victim has not settled for several epochs and holds a large userRewards balance, and drive `_calExpireForfeit(account,_amount)` out of agreement with `vlMGP.getRewardablePercentWAD(account)` - breaking the invariant that only the account or an operator acting on a real balance change may advance a user's reward index - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has not settled for several epochs and holds a large userRewards balance, then assert `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` end identical in both runs.
