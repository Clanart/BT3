# Q5238: WombatPoolHelper.withdraw - _minAmount is caller-supplied on a shared Wombat withdrawal

## Question
wombat/WombatPoolHelper.sol - withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Can an unprivileged attacker controlling _liquidity and _minAmount, with the payout measured as a balance delta, under the attacker has moved the wom/mWom Wombat pool immediately before calling, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` and the invariant that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: _minAmount is caller-supplied on a shared Wombat withdrawal)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() forwards the caller's _minAmount straight into the Wombat pool withdrawal, and WombatStaking then pays the caller the entire deposit-token balance delta, so a caller can accept an arbitrarily bad execution while the delta they receive is measured on a shared balance. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta) under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting on every row that the slippage floor for a withdrawal must protect the pool's shared balance, not only the caller.
