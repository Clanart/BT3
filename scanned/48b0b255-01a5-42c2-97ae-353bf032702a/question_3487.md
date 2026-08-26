# Q3487: MasterMagpie.multiclaimFor - unregistered staking token smuggled into _multiClaim

## Question
Note that in rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Can an attacker holding only tokens bought on market reach it via `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` under a large honest deposit is sitting in the mempool and the attacker sandwiches it and force `mgpPerSec` apart from `IERC20(mgp).balanceOf(masterMagpie)`, breaking the invariant that only pools actually added through add() may be routed through the claim classification and send branches for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `mgpPerSec` must stay reconciled with `IERC20(mgp).balanceOf(masterMagpie)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is sitting in the mempool and the attacker sandwiches it, call `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`, and assert `mgpPerSec` equals `IERC20(mgp).balanceOf(masterMagpie)` and that no account can withdraw more than it put in.
