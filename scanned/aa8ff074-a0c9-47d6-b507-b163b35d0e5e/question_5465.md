# Q5465: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
Note that in wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for)` under the receipt token is minted to the helper while the credit is directed at a different address and force `IERC20(stakingToken).balanceOf(address(this)) delta` apart from `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`, breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any address) and _amount, with _minimumLiquidity hardcoded to zero) under the receipt token is minted to the helper while the credit is directed at a different address, asserting on every row that a deposit path must carry a slippage floor even when the beneficiary is not the caller.
