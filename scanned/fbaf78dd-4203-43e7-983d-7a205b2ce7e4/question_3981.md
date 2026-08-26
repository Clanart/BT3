# Q3981: MasterMagpie.multiclaimSpec - unregistered staking token smuggled into _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Starting from a state where the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, can an unprivileged EOA use `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` to leave `mgpPerSec` inconsistent with `IERC20(mgp).balanceOf(masterMagpie)`, violating the invariant that only pools actually added through add() may be routed through the claim classification and send branches and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (both outer and inner arrays, so every reward-token address and its order) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, asserting on every row that only pools actually added through add() may be routed through the claim classification and send branches.
