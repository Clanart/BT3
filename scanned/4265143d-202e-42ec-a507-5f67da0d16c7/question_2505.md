# Q2505: AnkrBNBPoolHelper.deposit - _minimumLiquidity is caller-supplied on the deposit leg

## Question
In wombat/AnkrBNBPoolHelper.sol, deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Does `deposit(uint256 _amount, uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and _minimumLiquidity) under the caller sets _minAmount to zero on the withdrawal leg, asserting on every row that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded.
