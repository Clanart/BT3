# Q5225: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
In wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the attacker deposits and withdraws through the helper inside one transaction, so that `_minimumLiquidity supplied by the caller` diverges from `the LP actually minted by the Wombat pool`, the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the helper inside one transaction, then assert `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` end identical in both runs.
