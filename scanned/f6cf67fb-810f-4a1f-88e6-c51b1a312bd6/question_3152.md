# Q3152: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/vlMGPBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Starting from a state where the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged EOA use `updateFor(address _account)` to leave `balanceOf(account)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`, violating the invariant that only the account or an operator acting on a real balance change may advance a user's reward index and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, then assert `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` end identical in both runs.
