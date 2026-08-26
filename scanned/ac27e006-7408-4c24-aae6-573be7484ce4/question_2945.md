# Q2945: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelper.sol: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Under the caller sets _minAmount to zero on the withdrawal leg, is there an unprivileged sequence of `depositNative(uint256 _minimumLiquidity)` that leaves `_minimumLiquidity supplied by the caller` unreconciled with `the LP actually minted by the Wombat pool`, violates the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the caller sets _minAmount to zero on the withdrawal leg, have the attacker run `depositNative(uint256 _minimumLiquidity)`, then assert the victim's claimable value and the `_minimumLiquidity supplied by the caller` versus `the LP actually minted by the Wombat pool` relation are unchanged by the attacker's transaction.
