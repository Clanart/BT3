# Q4612: WombatPoolHelperV2.deposit - depositFor hardcodes _minimumLiquidity to zero

## Question
In wombat/WombatPoolHelperV2.sol, depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Can an unprivileged attacker reach this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` while an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
