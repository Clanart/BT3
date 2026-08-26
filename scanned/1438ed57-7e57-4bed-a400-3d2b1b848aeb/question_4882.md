# Q4882: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
In wombat/WombatPoolHelper.sol, deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
