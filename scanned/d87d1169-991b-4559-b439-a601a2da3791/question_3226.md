# Q3226: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
Note that in wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under a residual stakingToken balance from an earlier rounding sits on the helper and force `pid cached at construction` apart from `pools[lpToken].pid in WombatStaking`, breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual stakingToken balance from an earlier rounding sits on the helper, call `deposit(uint256 _amount, uint256 _minimumLiquidity)`, and assert `pid cached at construction` equals `pools[lpToken].pid in WombatStaking` and that no account can withdraw more than it put in.
