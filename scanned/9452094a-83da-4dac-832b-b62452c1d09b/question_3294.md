# Q3294: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
In wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Starting from a state where a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged EOA use `depositFor(uint256 _amount, address _for)` to leave `IERC20(stakingToken).balanceOf(address(this)) delta` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, violating the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any address) and _amount, with _minimumLiquidity hardcoded to zero) under a residual stakingToken balance from an earlier rounding sits on the helper, asserting on every row that a deposit path must carry a slippage floor even when the beneficiary is not the caller.
