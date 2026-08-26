# Q5085: WombatPoolHelperV2.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/WombatPoolHelperV2.sol - deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has moved the wom/mWom Wombat pool immediately before calling, call `depositNative(uint256 _minimumLiquidity)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
