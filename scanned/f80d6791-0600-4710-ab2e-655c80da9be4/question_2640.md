# Q2640: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. With the victim address and the block at which their bribe index is pinned under attacker control and the bribe token has begun reverting on transfer, can an unprivileged caller sequence `updateFor(address _account) inherited from BaseRewardPoolV2` so that `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` no longer reconcile, violating the invariant that the scaling factor must match the unit the balance ledger is denominated in and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under the bribe token has begun reverting on transfer, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `totalSupply at the moment of the flush` and the PoC's balance delta is non-positive.
