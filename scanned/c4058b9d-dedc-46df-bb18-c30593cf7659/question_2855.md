# Q2855: BribeRewardPool.withdrawFor - scaling factor taken from an unrelated staking token

## Question
Note that in rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
