# Q5212: AnkrBNBPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Assuming the attacker deposits and withdraws through the helper inside one transaction, can an unprivileged attacker turn this into a divergence between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker deposits and withdraws through the helper inside one transaction, then assert `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` end identical in both runs.
