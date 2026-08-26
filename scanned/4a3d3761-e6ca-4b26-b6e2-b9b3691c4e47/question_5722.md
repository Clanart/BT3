# Q5722: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
rewards/MasterMagpie.sol: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. With _account (any victim), the staking-token list and the per-pool reward-token lists under attacker control and the contract is paused so only emergencyWithdraw is reachable, can an unprivileged caller sequence `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` so that `totalAllocPoint` and `tokenToPoolInfo[_stakingToken].allocPoint` no longer reconcile, violating the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `totalAllocPoint` must stay reconciled with `tokenToPoolInfo[_stakingToken].allocPoint`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract is paused so only emergencyWithdraw is reachable, call `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, and assert `totalAllocPoint` equals `tokenToPoolInfo[_stakingToken].allocPoint` and that no account can withdraw more than it put in.
