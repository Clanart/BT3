# Q0476: WombatPoolHelper.depositNative - _minimumLiquidity is caller-supplied on the deposit leg

## Question
Consider wombat/WombatPoolHelper.sol, where deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` via `depositNative(uint256 _minimumLiquidity)`, breaking the invariant that a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: _minimumLiquidity is caller-supplied on the deposit leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: deposit() and depositNative() forward the caller's _minimumLiquidity into the Wombat deposit, so a caller can accept a deposit that mints far less LP than fair while the receipt mint is taken from the resulting delta. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a deposit must not be able to credit receipt tokens against an execution the caller deliberately degraded; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `depositNative(uint256 _minimumLiquidity)` sequence atomically under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting at the end that `_liquidity burned via burnReceiptToken` still equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and the PoC's balance delta is non-positive.
