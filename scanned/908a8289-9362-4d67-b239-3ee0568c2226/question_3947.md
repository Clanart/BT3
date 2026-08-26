# Q3947: AnkrBNBPoolHelper.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/AnkrBNBPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _liquidity, _minAmount and the ordering against the lockedAmount check under attacker control and the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `this.balance(msg.sender)` and `lockedAmount[msg.sender]` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `this.balance(msg.sender)` equals `lockedAmount[msg.sender]` and that no account can withdraw more than it put in.
