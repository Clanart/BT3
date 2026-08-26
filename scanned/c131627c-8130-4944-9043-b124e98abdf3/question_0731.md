# Q0731: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that the scaling factor must match the unit the balance ledger is denominated in and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker votes and casts inside one transaction through voteAndCast, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
