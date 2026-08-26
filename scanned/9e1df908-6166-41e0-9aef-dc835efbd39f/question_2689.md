# Q2689: vlMGPBaseRewarder.updateFor - permissionless updateFor pins a victim's index

## Question
rewards/vlMGPBaseRewarder.sol - updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under a large MGP distribution has just been queued and no account has settled yet, exploit this through `updateFor(address _account)` to break the reconciliation between `totalStaked()` and `IERC20(vlMGP).totalSupply()` and the invariant that only the account or an operator acting on a real balance change may advance a user's reward index, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: permissionless updateFor pins a victim's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: updateFor(address) has no access control and writes userRewards and userRewardPerTokenPaid for any account, so an attacker fixes a victim's accrual before a forfeit-bearing settlement. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: only the account or an operator acting on a real balance change may advance a user's reward index; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `updateFor(address _account)`, and assert `totalStaked()` equals `IERC20(vlMGP).totalSupply()` and that no account can withdraw more than it put in.
