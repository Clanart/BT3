# Q4528: WombatBribeManager.unvote - unvote reverts for exactly the pools it was written to rescue

## Question
Consider wombat/WombatBribeManager.sol, where the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Assuming delegatedPool is unset so the delegate legs are skipped, can an unprivileged attacker turn this into a divergence between `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` via `unvote(address _lp)`, breaking the invariant that a user must always retain a path to release a vote commitment, especially on a pool that is no longer active and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: unvote reverts for exactly the pools it was written to rescue)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: the comment on unvote() says it exists so that deleting a pool or changing a rewarder does not block withdrawals, but the body reverts with PoolNotActive when pool.isActive is false, which is the inverse of the stated intent. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a user must always retain a path to release a vote commitment, especially on a pool that is no longer active; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish delegatedPool is unset so the delegate legs are skipped, have the attacker run `unvote(address _lp)`, then assert the victim's claimable value and the `earnedRewards reported by claimAllBribes` versus `the tokens actually transferred by getReward` relation are unchanged by the attacker's transaction.
