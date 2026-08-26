# Q4719: WombatPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
wombat/WombatPoolHelper.sol: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, is there an unprivileged sequence of `deposit(uint256 _amount, uint256 _minimumLiquidity)` that leaves `this.balance(msg.sender)` unreconciled with `lockedAmount[msg.sender]`, violates the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, call `deposit(uint256 _amount, uint256 _minimumLiquidity)`, and assert `this.balance(msg.sender)` equals `lockedAmount[msg.sender]` and that no account can withdraw more than it put in.
