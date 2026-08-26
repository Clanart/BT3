# Q3153: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/mWOMSVBaseRewarder.sol - updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, exploit this through `updateFor(address _account)` to break the reconciliation between `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and the invariant that only the account or an operator acting on a real balance change may advance a user's reward index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
