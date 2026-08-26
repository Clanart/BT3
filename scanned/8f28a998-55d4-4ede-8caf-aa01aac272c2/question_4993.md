# Q4993: WombatPoolHelperV2.depositFor - safeApprove without reset before depositFor into MasterMagpie

## Question
wombat/WombatPoolHelperV2.sol: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that an approval on the deposit hot path must be idempotent, and delivers High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `depositFor(uint256 _amount, address _for)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
