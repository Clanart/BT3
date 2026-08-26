# Q2095: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Under the bribe token registered for this gauge charges a transfer fee, is there an unprivileged sequence of `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that the scaling factor must match the unit the balance ledger is denominated in, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence atomically under the bribe token registered for this gauge charges a transfer fee, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
