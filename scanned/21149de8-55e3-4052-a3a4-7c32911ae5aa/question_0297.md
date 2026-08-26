# Q0297: BribeRewardPool.withdrawFor - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker reach this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` while a large bribe for the gauge is pending and no cast has run yet, and drive `userRewards[_rewardToken][account]` out of agreement with `earned(account,_rewardToken)` - breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that a large bribe for the gauge is pending and no cast has run yet, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that the scaling factor must match the unit the balance ledger is denominated in.
