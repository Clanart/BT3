# Q3451: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
rewards/BribeRewardPool.sol: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and the attacker calls the inherited donateRewards for the registered bribe token, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` no longer reconcile, violating the invariant that the scaling factor must match the unit the balance ledger is denominated in and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote) under the attacker calls the inherited donateRewards for the registered bribe token, asserting on every row that the scaling factor must match the unit the balance ledger is denominated in.
