# Q2131: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `userRewards[_rewardToken][account]` apart from `rewards[_rewardToken].rewardPerTokenStored`, breaking the invariant that only the account or an operator acting on a real balance change may advance a user's reward index for High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the computed forfeit lands just above the _amount / 1000 dust threshold, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that only the account or an operator acting on a real balance change may advance a user's reward index.
