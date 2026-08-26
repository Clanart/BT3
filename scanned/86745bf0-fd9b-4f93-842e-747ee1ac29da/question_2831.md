# Q2831: WombatPoolHelper.depositLP - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/WombatPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `_minimumLiquidity supplied by the caller` diverges from `the LP actually minted by the Wombat pool`, the invariant that an approval on the deposit hot path must be idempotent is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under the caller sets _minAmount to zero on the withdrawal leg, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
