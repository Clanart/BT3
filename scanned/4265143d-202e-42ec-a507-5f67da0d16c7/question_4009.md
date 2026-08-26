# Q4009: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
Consider rewards/BribeRewardPool.sol, where the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Assuming the victim has a large unsettled bribe balance, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled bribe balance, have the attacker run `updateFor(address _account) inherited from BaseRewardPoolV2`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.
