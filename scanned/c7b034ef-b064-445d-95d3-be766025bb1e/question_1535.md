# Q1535: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
Consider rewards/vlMGPBaseRewarder.sol, where updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Assuming the computed forfeit lands just below the _amount / 1000 dust threshold, can an unprivileged attacker turn this into a divergence between `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)` via `updateFor(address _account)`, breaking the invariant that only the account or an operator acting on a real balance change may advance a user's reward index and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the computed forfeit lands just below the _amount / 1000 dust threshold, snapshot `_calExpireForfeit(account,_amount)` and `vlMGP.getRewardablePercentWAD(account)`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
