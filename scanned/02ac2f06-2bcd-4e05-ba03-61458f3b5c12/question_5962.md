# Q5962: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
rewards/MasterMagpie.sol: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. With _account (any victim), the staking-token list and the per-pool reward-token lists under attacker control and the attacker repeats the call in the same block to observe the second, no-op iteration, can an unprivileged caller sequence `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the attacker repeats the call in the same block to observe the second, no-op iteration.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_account (any victim), the staking-token list and the per-pool reward-token lists) under the attacker repeats the call in the same block to observe the second, no-op iteration, asserting on every row that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state.
