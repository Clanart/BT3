# Q2695: WombatPoolHelperV2.depositFor - safeApprove without reset before depositFor into MasterMagpie

## Question
In wombat/WombatPoolHelperV2.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Does `depositFor(uint256 _amount, address _for)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `_minimumLiquidity supplied by the caller` diverges from `the LP actually minted by the Wombat pool`, the invariant that an approval on the deposit hot path must be idempotent is broken, and the result is High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minAmount to zero on the withdrawal leg, call `depositFor(uint256 _amount, address _for)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
