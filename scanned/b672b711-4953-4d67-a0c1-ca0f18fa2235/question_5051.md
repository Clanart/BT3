# Q5051: MasterMagpie.multiclaimFor - forced claim of a victim through permissionless multiclaimFor

## Question
Consider rewards/MasterMagpie.sol, where multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Assuming the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged attacker turn this into a divergence between `_calLpSupply(_stakingToken)` and `IERC20(_stakingToken).balanceOf(masterMagpie)` via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, breaking the invariant that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: forced claim of a victim through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: multiclaimFor(_stakingTokens, _rewardTokens, _account) has no access control and no msg.sender == _account check, so any address can force a settlement on any victim at a timestamp of the attacker's choosing. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state; concretely, `_calLpSupply(_stakingToken)` must stay reconciled with `IERC20(_stakingToken).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_account (any victim), the staking-token list and the per-pool reward-token lists) under the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, asserting on every row that only the account itself, or a contract it authorized, may decide when its rewards are settled and at what forfeit/lock state.
