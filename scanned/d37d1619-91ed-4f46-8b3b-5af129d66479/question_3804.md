# Q3804: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
wombat/WombatPoolHelperV2.sol: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. With _for (any address) and _amount, with _minimumLiquidity hardcoded to zero under attacker control and the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for)` so that `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` no longer reconcile, violating the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any address) and _amount, with _minimumLiquidity hardcoded to zero) under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, asserting on every row that a deposit path must carry a slippage floor even when the beneficiary is not the caller.
