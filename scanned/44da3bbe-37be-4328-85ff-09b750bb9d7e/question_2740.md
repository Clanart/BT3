# Q2740: BribeRewardPool.stakeFor - scaling factor taken from an unrelated staking token

## Question
Consider rewards/BribeRewardPool.sol, where the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Assuming the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, breaking the invariant that the scaling factor must match the unit the balance ledger is denominated in and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: scaling factor taken from an unrelated staking token)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: the inherited _provisionReward scales by 10**stakingTokenDecimals, where stakingToken was fixed at construction and is unrelated to the vlMGP-denominated vote balances this pool actually tracks. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: the scaling factor must match the unit the balance ledger is denominated in; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]`, run the attacker's `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
