# Q3213: BribeRewardPool.withdrawFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol - the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an unprivileged attacker controlling the negative delta and whether the claim leg runs, under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, exploit this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that the scaling factor must match the unit the balance ledger is denominated in, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, have the attacker run `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, then assert the victim's claimable value and the `rewards[_rewardToken].queuedRewards` versus `totalSupply at the moment of the flush` relation are unchanged by the attacker's transaction.
