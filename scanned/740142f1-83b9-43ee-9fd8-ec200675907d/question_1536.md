# Q1536: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
In rewards/mWOMSVBaseRewarder.sol, updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Starting from a state where the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged EOA use `updateFor(address _account)` to leave `_calExpireForfeit(account,_amount)` inconsistent with `mWOMSV.getRewardablePercentWAD(account)`, violating the invariant that only the account or an operator acting on a real balance change may advance a user's reward index and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the computed forfeit lands just below the _amount / 1000 dust threshold, call `updateFor(address _account)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
