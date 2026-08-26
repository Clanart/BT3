# Q3295: AnkrBNBPoolHelper.depositNative - safeApprove without reset before depositFor into MasterMagpie

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Can an attacker holding only tokens bought on market reach it via `depositNative(uint256 _minimumLiquidity)` under a residual stakingToken balance from an earlier rounding sits on the helper and force `IERC20(stakingToken).balanceOf(address(this)) delta` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, breaking the invariant that an approval on the deposit hot path must be idempotent for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: safeApprove without reset before depositFor into MasterMagpie)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: _stake() calls IERC20(stakingToken).safeApprove(masterMagpie, _amount) with no prior zeroing, so any allowance residue left by a depositFor that under-consumes permanently disables deposits through this helper. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: an approval on the deposit hot path must be idempotent; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up a residual stakingToken balance from an earlier rounding sits on the helper, snapshot `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
