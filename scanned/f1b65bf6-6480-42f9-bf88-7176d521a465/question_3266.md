# Q3266: MasterMagpie.multiclaimSpec - rewardDebt reset without reward payout in _multiClaim

## Question
Note that in rewards/MasterMagpie.sol, _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Can an attacker holding only tokens bought on market reach it via `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` under a large honest deposit is sitting in the mempool and the attacker sandwiches it and force `vlmgp.totalSupply()` apart from `sum of userInfo[vlmgp][*].amount`, breaking the invariant that no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: rewardDebt reset without reward payout in _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Precondition: a large honest deposit is sitting in the mempool and the attacker sandwiches it.
- Invariant to test: no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large honest deposit is sitting in the mempool and the attacker sandwiches it, call `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`, and assert `vlmgp.totalSupply()` equals `sum of userInfo[vlmgp][*].amount` and that no account can withdraw more than it put in.
