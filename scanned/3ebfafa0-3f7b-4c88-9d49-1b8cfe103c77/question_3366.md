# Q3366: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, is there an unprivileged sequence of `updateFor(address _account) inherited from BaseRewardPoolV2` that leaves `totalSupply` unreconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`, violates the invariant that the scaling factor must match the unit the balance ledger is denominated in, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, asserting at the end that `totalSupply` still equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the PoC's balance delta is non-positive.
