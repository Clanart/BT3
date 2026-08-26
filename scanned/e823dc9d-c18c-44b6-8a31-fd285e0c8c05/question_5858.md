# Q5858: MasterMagpie.multiclaim - unregistered staking token smuggled into _multiClaim

## Question
rewards/MasterMagpie.sol: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Under the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, is there an unprivileged sequence of `multiclaim(address[] _stakingTokens)` that leaves `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` unreconciled with `block.timestamp`, violates the invariant that only pools actually added through add() may be routed through the claim classification and send branches, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: unregistered staking token smuggled into _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() reads tokenToPoolInfo[_stakingToken] for arbitrary caller-supplied addresses with no registeredToken membership check, so a never-added address yields a zero PoolInfo whose rewarder and accMGPPerShare are zero while the classification branches (vlmgp / MPGRewardPool / default) still execute. Precondition: the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18.
- Invariant to test: only pools actually added through add() may be routed through the claim classification and send branches; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the victim is mid-cooldown in VLMGP so getRewardablePercentWAD is still 1e18, snapshot `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp`, run the attacker's `multiclaim(address[] _stakingTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
