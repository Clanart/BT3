# Q3023: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
In rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker reach this through `updateFor(address _account) inherited from BaseRewardPoolV2` while the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, and drive `_balances[account]` out of agreement with `totalSupply` - breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in - for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, then assert `_balances[account]` and `totalSupply` end identical in both runs.
