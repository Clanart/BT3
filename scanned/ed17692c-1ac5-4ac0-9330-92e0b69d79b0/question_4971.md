# Q4971: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
wombat/WombatPoolHelperV2.sol: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `deposit(uint256 _amount, uint256 _minimumLiquidity)` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has moved the wom/mWom Wombat pool immediately before calling, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
