# Q2600: BribeRewardPool.updateFor - inherited updateFor pins any voter's index

## Question
rewards/BribeRewardPool.sol: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Under the bribe token has begun reverting on transfer, is there an unprivileged sequence of `updateFor(address _account) inherited from BaseRewardPoolV2` that leaves `rewards[_rewardToken].rewardPerTokenStored` unreconciled with `userRewardPerTokenPaid[_rewardToken][account]`, violates the invariant that only the account or the operator on a real vote change may advance a voter's index, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/BribeRewardPool.sol -> `updateFor(address _account) inherited from BaseRewardPoolV2` (mechanism: inherited updateFor pins any voter's index)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account) inherited from BaseRewardPoolV2`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their bribe index is pinned
- Exploit idea: updateFor(address) is inherited without access control, so an attacker fixes a victim voter's bribe accrual at a chosen block ahead of a cast. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: only the account or the operator on a real vote change may advance a voter's index; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account) inherited from BaseRewardPoolV2` sequence atomically under the bribe token has begun reverting on transfer, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
