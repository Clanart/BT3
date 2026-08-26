# Q5253: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
Consider wombat/WombatPoolHelperV2.sol, where depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Assuming the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `depositFor(uint256 _amount, address _for)`, breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the helper inside one transaction, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
