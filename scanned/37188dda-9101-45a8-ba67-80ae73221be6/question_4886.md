# Q4886: MasterMagpie.multiclaim - rewardDebt reset without reward payout in _multiClaim

## Question
rewards/MasterMagpie.sol: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. With the full _stakingTokens array, including duplicates and unregistered addresses under attacker control and the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, can an unprivileged caller sequence `multiclaim(address[] _stakingTokens)` so that `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` no longer reconcile, violating the invariant that no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: rewardDebt reset without reward payout in _multiClaim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _multiClaim() sets user.rewardDebt = user.amount * accMGPPerShare / 1e12 and zeroes unClaimedMgp for every entry in the caller-supplied _stakingTokens array before the send branch decides where the MGP goes, so a claim path that silently pays nothing still burns the accrual. Precondition: the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals.
- Invariant to test: no code path may advance rewardDebt or clear unClaimedMgp unless the corresponding MGP actually leaves the contract to the user or is locked for them; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the staking token is a Wombat receipt token minted by WombatStaking with 18 decimals, snapshot `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)`, run the attacker's `multiclaim(address[] _stakingTokens)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
