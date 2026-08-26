# Q1221: DelegateVoteRewardPool.harvestAll - queued backlog released to whoever holds balance at the flush

## Question
Note that in rewards/DelegateVoteRewardPool.sol, _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Can an attacker holding only tokens bought on market reach it via `harvestAll()` under the attacker obtains delegate-pool balance in the block before a large bribe lands and force `rewards[_rewardToken].rewardPerTokenStored` apart from `totalSupply of the delegate pool`, breaking the invariant that a backlog accrued while the pool was empty must not be assignable to one holder for Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: queued backlog released to whoever holds balance at the flush)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: _queueNewRewardsWithoutTransfer() accumulates into queuedRewards while totalSupply is zero and releases the whole backlog on the next call, so a single one-block holder can absorb it. Precondition: the attacker obtains delegate-pool balance in the block before a large bribe lands.
- Invariant to test: a backlog accrued while the pool was empty must not be assignable to one holder; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker obtains delegate-pool balance in the block before a large bribe lands, call `harvestAll()`, and assert `rewards[_rewardToken].rewardPerTokenStored` equals `totalSupply of the delegate pool` and that no account can withdraw more than it put in.
