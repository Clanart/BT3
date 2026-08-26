# Q3298: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
Consider rewards/BribeRewardPool.sol, where the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Assuming the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amountReward and which already-registered bribe token is provisioned) under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, asserting on every row that the scaling factor must match the unit the balance ledger is denominated in.
