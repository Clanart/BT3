# Q1504: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
In rewards/MasterMagpie.sol, _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Does `multiclaim(address[] _stakingTokens)` let an unprivileged caller exploit that under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, so that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` diverges from `block.timestamp`, the invariant that only pools actually added through add() may be routed through the claim classification and send branches is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaim(address[] _stakingTokens)` sequence atomically under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, asserting at the end that `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` still equals `block.timestamp` and the PoC's balance delta is non-positive.
