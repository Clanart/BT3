# Q3643: WombatPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
wombat/WombatPoolHelper.sol: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. With _liquidity and _minAmount, with the payout measured as a balance delta under attacker control and a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a residual stakingToken balance from an earlier rounding sits on the helper, then assert `pid cached at construction` and `pools[lpToken].pid in WombatStaking` end identical in both runs.
