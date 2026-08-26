# Q5259: WombatPoolHelper.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _liquidity and _minAmount, with the payout measured as a balance delta under attacker control and the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `pid cached at construction` and `pools[lpToken].pid in WombatStaking` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker has moved the wom/mWom Wombat pool immediately before calling, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
