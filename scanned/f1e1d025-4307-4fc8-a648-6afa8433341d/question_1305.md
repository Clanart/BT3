# Q1305: DelegateVoteRewardPool.getReward - getReward is public and settles any account

## Question
rewards/DelegateVoteRewardPool.sol: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Under the attacker obtains delegate-pool balance in the block before a large bribe lands, is there an unprivileged sequence of `getReward(address _for)` that leaves `_balances[account]` unreconciled with `totalSupply`, violates the invariant that only the account itself may decide when its rewards are settled, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: getReward is public and settles any account)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: getReward(address _for) is public with only the updateRewards modifier, so any caller can force a settlement of any delegate-pool participant at a chosen block. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: only the account itself may decide when its rewards are settled; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _for)` sequence atomically under the attacker obtains delegate-pool balance in the block before a large bribe lands, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
