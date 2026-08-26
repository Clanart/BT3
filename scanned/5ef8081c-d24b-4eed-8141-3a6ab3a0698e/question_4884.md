# Q4884: AnkrBNBPoolHelper.deposit - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/AnkrBNBPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `deposit(uint256 _amount, uint256 _minimumLiquidity)` that leaves `this.balance(msg.sender)` unreconciled with `lockedAmount[msg.sender]`, violates the invariant that a helper must revalidate the pool identity it acts on before moving value, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has moved the wom/mWom Wombat pool immediately before calling, then assert `this.balance(msg.sender)` and `lockedAmount[msg.sender]` end identical in both runs.
