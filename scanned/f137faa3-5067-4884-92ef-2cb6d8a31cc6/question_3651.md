# Q3651: BaseRewardPoolV2.updateFor - early-continue skips a genuine balance change

## Question
In rewards/BaseRewardPoolV2.sol, the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Does `updateFor(address _account)` let an unprivileged caller exploit that under the victim has not been settled for several epochs and holds a large userRewards balance, so that `balanceOf(account)` diverges from `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, the invariant that userRewards must capture every balance-weighted segment, including segments where the global index did not move is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: early-continue skips a genuine balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: the userRewardPerTokenPaid == rewardPerToken early-continue treats an unchanged global index as proof that nothing is owed, but balanceOf(account) is read live from MasterMagpie, so a balance that changed while the index stood still is never folded into userRewards. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: userRewards must capture every balance-weighted segment, including segments where the global index did not move; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has not been settled for several epochs and holds a large userRewards balance, call `updateFor(address _account)`, and assert `balanceOf(account)` equals `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
