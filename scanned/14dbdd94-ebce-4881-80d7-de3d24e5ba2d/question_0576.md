# Q0576: BribeRewardPool.updateFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. With the victim address and the block at which their bribe index is pinned under attacker control and a large bribe for the gauge is pending and no cast has run yet, can an unprivileged caller sequence `updateFor(address _account) inherited from BaseRewardPoolV2` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that the scaling factor must match the unit the balance ledger is denominated in and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large bribe for the gauge is pending and no cast has run yet, call `updateFor(address _account) inherited from BaseRewardPoolV2`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
