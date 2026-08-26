# Q4438: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Starting from a state where the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, can an unprivileged EOA use `multiclaim(address[] _stakingTokens)` to leave `mgpPerSec` inconsistent with `IERC20(mgp).balanceOf(masterMagpie)`, violating the invariant that only pools actually added through add() may be routed through the claim classification and send branches and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaim(address[] _stakingTokens)`: constrain the setup so that the pool's rewarder is a V2 rewards/BaseRewardPoolV2.sol that caches stakingTokenDecimals at construction, fuzz the attacker inputs (the full _stakingTokens array, including duplicates and unregistered addresses), and assert after every call that only pools actually added through add() may be routed through the claim classification and send branches.
