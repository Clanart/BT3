# Q1624: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` while totalSupply is zero because every voter has unvoted, and drive `totalSupply` out of agreement with `the sum of userVotedForPoolInVlmgp over all voters for this pool` - breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalSupply is zero because every voter has unvoted, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
