# Q5015: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
wombat/WombatPoolHelperV2.sol - depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an unprivileged attacker controlling _for (any address) and _amount, with _minimumLiquidity hardcoded to zero, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `depositFor(uint256 _amount, address _for)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for)`: constrain the setup so that the attacker has moved the wom/mWom Wombat pool immediately before calling, fuzz the attacker inputs (_for (any address) and _amount, with _minimumLiquidity hardcoded to zero), and assert after every call that a deposit path must carry a slippage floor even when the beneficiary is not the caller.
