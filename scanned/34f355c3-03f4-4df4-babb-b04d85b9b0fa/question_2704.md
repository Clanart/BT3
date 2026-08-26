# Q2704: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
rewards/MasterMagpie.sol: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, is there an unprivileged sequence of `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` that leaves `userInfo[_stakingToken][user].amount` unreconciled with `_calLpSupply(_stakingToken)`, violates the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, snapshot `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)`, run the attacker's `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
