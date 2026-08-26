# Q3531: WombatPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
Consider wombat/WombatPoolHelper.sol, where _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Assuming a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that an approval on the deposit hot path must be idempotent and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Two-account fork test (victim and attacker): establish a residual stakingToken balance from an earlier rounding sits on the helper, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `IERC20(stakingToken).balanceOf(address(this)) delta` versus `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` relation are unchanged by the attacker's transaction.
