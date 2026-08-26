# Q4326: mWOMSVBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/mWOMSVBaseRewarder.sol: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. With the victim address and the block at which their index is pinned under attacker control and the victim has not settled for several epochs and holds a large userRewards balance, can an unprivileged caller sequence `updateFor(address _account)` so that `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that only the account or an operator acting on a real balance change may advance a user's reward index and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the victim has not settled for several epochs and holds a large userRewards balance.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the victim has not settled for several epochs and holds a large userRewards balance, asserting at the end that `_calExpireForfeit(account,_amount)` still equals `mWOMSV.getRewardablePercentWAD(account)` and the PoC's balance delta is non-positive.
