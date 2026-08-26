# Q4996: MasterMagpie.multiclaimSpec - unregistered staking token smuggled into _multiClaim

## Question
Consider rewards/MasterMagpie.sol, where _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Assuming the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged attacker turn this into a divergence between `userInfo[_stakingToken][user].available` and `userInfo[_stakingToken][user].amount` via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, breaking the invariant that only pools actually added through add() may be routed through the claim classification and send branches and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, call `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, and assert `userInfo[_stakingToken][user].available` equals `userInfo[_stakingToken][user].amount` and that no account can withdraw more than it put in.
