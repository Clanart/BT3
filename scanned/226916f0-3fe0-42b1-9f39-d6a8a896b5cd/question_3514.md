# Q3514: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
In wombat/WombatPoolHelper.sol, deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker reach this through `depositNative(uint256 _minimumLiquidity)` while a residual stakingToken balance from an earlier rounding sits on the helper, and drive `pid cached at construction` out of agreement with `pools[lpToken].pid in WombatStaking` - breaking the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual stakingToken balance from an earlier rounding sits on the helper, call `depositNative(uint256 _minimumLiquidity)`, and assert `pid cached at construction` equals `pools[lpToken].pid in WombatStaking` and that no account can withdraw more than it put in.
