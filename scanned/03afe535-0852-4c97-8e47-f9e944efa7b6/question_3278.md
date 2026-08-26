# Q3278: AnkrBNBPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
wombat/AnkrBNBPoolHelper.sol - deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Can an unprivileged attacker controlling msg.value and _minimumLiquidity, under a residual stakingToken balance from an earlier rounding sits on the helper, exploit this through `depositNative(uint256 _minimumLiquidity)` to break the reconciliation between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` and the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a residual stakingToken balance from an earlier rounding sits on the helper, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
