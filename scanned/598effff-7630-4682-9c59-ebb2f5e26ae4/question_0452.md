# Q0452: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol - the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker controlling _amountReward and which already-registered bribe token is provisioned, under a large bribe for the gauge is pending and no cast has run yet, exploit this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that the scaling factor must match the unit the balance ledger is denominated in, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe for the gauge is pending and no cast has run yet, then assert `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` end identical in both runs.
