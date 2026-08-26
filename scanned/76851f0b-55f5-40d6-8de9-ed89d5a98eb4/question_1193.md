# Q1193: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
Consider rewards/BribeRewardPool.sol, where the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Assuming the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged attacker turn this into a divergence between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker votes and casts inside one transaction through voteAndCast, then assert `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` end identical in both runs.
