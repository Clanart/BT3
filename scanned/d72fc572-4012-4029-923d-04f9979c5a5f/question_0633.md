# Q0633: AnkrBNBPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Under the pool's deposit token is wBNB and the caller arrived through depositNative, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check) under the pool's deposit token is wBNB and the caller arrived through depositNative, asserting on every row that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller.
