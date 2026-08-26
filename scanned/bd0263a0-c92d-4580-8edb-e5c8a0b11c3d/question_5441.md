# Q5441: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
In wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the receipt token is minted to the helper while the credit is directed at a different address, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the receipt token is minted to the helper while the credit is directed at a different address, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
