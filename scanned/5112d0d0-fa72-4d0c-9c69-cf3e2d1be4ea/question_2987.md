# Q2987: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
Consider rewards/BribeRewardPool.sol, where updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Assuming the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` via `updateFor(address _account) inherited from BaseRewardPoolV2`, breaking the invariant that only the account or the operator on a real vote change may advance a voter's index and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, snapshot `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)`, run the attacker's `updateFor(address _account) inherited from BaseRewardPoolV2` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
