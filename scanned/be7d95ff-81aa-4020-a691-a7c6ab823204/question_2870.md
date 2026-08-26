# Q2870: WombatPoolHelperV2.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
In wombat/WombatPoolHelperV2.sol, deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Does `depositNative(uint256 _minimumLiquidity)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `pid cached at construction` diverges from `pools[lpToken].pid in WombatStaking`, the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minAmount to zero on the withdrawal leg, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `depositNative(uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
