# Q1493: DelegateVoteRewardPool.harvestAll - no reentrancy guard on harvestAll

## Question
In rewards/DelegateVoteRewardPool.sol, harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Does `harvestAll()` let an unprivileged caller exploit that under protocolFee is non-zero and feeCollector is set, so that `rewards[_rewardToken].rewardPerTokenStored` diverges from `totalSupply of the delegate pool`, the invariant that a function that settles from external claim results must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `harvestAll()` (mechanism: no reentrancy guard on harvestAll)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvestAll()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the delegate pool claims and re-queues every bribe, callable by anyone
- Exploit idea: harvestAll() performs an external claim across every pool and then external fee transfers with no nonReentrant, so a bribe token with a transfer hook re-enters between the claim and the queue. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: a function that settles from external claim results must hold a reentrancy guard; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up protocolFee is non-zero and feeCollector is set, snapshot `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool`, run the attacker's `harvestAll()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
