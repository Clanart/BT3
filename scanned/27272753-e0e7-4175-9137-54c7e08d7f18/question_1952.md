# Q1952: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
wombat/WombatPoolHelperV2.sol - depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an unprivileged attacker controlling _amount and _minimumLiquidity, under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, exploit this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _minimumLiquidity) under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting on every row that a deposit path must carry a slippage floor even when the beneficiary is not the caller.
