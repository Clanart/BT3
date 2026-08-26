# Q1072: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker reach this through `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` while the attacker votes and casts inside one transaction through voteAndCast, and drive `_balances[account]` out of agreement with `totalSupply` - breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker votes and casts inside one transaction through voteAndCast, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
