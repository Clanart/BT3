# Q0477: WombatPoolHelperV2.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
Consider wombat/WombatPoolHelperV2.sol, where the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` via `depositLP(uint256 _lpAmount)`, breaking the invariant that a helper must revalidate the pool identity it acts on before moving value and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the pool's deposit token is wBNB and the caller arrived through depositNative, have the attacker run `depositLP(uint256 _lpAmount)`, then assert the victim's claimable value and the `this.balance(msg.sender)` versus `lockedAmount[msg.sender]` relation are unchanged by the attacker's transaction.
