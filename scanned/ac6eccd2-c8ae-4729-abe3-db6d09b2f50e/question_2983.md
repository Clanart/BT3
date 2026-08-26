# Q2983: WombatPoolHelperV2.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
Note that in wombat/WombatPoolHelperV2.sol, withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 _liquidity, uint256 _minAmount)` under the caller sets _minAmount to zero on the withdrawal leg and force `pid cached at construction` apart from `pools[lpToken].pid in WombatStaking`, breaking the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minAmount to zero on the withdrawal leg, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
