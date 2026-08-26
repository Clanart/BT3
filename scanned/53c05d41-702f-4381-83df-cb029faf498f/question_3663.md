# Q3663: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
Note that in rewards/BribeRewardPool.sol, updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account) inherited from BaseRewardPoolV2` under the attacker calls the inherited donateRewards for the registered bribe token and force `_balances[account]` apart from `totalSupply`, breaking the invariant that only the account or the operator on a real vote change may advance a voter's index for High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the attacker calls the inherited donateRewards for the registered bribe token.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their bribe index is pinned) under the attacker calls the inherited donateRewards for the registered bribe token, asserting on every row that only the account or the operator on a real vote change may advance a voter's index.
