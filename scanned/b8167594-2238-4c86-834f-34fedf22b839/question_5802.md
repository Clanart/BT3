# Q5802: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
rewards/MasterMagpie.sol: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. With _account (any victim), the staking-token list and the per-pool reward-token lists under attacker control and the victim has a large unClaimedMgp balance that has not been settled for several epochs, can an unprivileged caller sequence `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` so that `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` no longer reconcile, violating the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the victim has a large unClaimedMgp balance that has not been settled for several epochs.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unClaimedMgp balance that has not been settled for several epochs, have the attacker run `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, then assert the victim's claimable value and the `vlmgp.totalSupply()` versus `sum of userInfo[vlmgp][*].amount` relation are unchanged by the attacker's transaction.
