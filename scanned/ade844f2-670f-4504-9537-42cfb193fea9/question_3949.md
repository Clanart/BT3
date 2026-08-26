# Q3949: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Does `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` let an unprivileged caller exploit that under the victim has a large unsettled bribe balance, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `userRewardPerTokenPaid[_rewardToken][account]`, the invariant that the scaling factor must match the unit the balance ledger is denominated in is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled bribe balance, call `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `userRewardPerTokenPaid[_rewardToken][account]` and that no account can withdraw more than it put in.
