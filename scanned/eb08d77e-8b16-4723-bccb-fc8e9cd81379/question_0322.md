# Q0322: WombatPoolHelperV2.depositFor - depositFor hardcodes _minimumLiquidity to zero

## Question
wombat/WombatPoolHelperV2.sol: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Under the pool's deposit token is wBNB and the caller arrived through depositNative, is there an unprivileged sequence of `depositFor(uint256 _amount, address _for)` that leaves `this.balance(msg.sender)` unreconciled with `lockedAmount[msg.sender]`, violates the invariant that a deposit path must carry a slippage floor even when the beneficiary is not the caller, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor hardcodes _minimumLiquidity to zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: depositFor() calls _deposit(_amount, 0, _for, address(this)) with the Wombat minimum liquidity pinned to zero, so a deposit routed through it accepts any execution the pool gives, and the receipt mint is taken from whatever delta results. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a deposit path must carry a slippage floor even when the beneficiary is not the caller; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool's deposit token is wBNB and the caller arrived through depositNative, have the attacker run `depositFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
