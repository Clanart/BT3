# Q2950: BribeRewardPool.donateRewards - scaling factor taken from an unrelated staking token

## Question
Note that in rewards/BribeRewardPool.sol, the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Can an attacker holding only tokens bought on market reach it via `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances and force `rewards[_rewardToken].queuedRewards` apart from `totalSupply at the moment of the flush`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in for Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountReward and which already-registered bribe token is provisioned
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, snapshot `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush`, run the attacker's `donateRewards(uint256 _amountReward, address _rewardToken) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
