# Q4589: AnkrBNBPoolHelper.depositLP - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/AnkrBNBPoolHelper.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _lpAmount under attacker control and an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `this.balance(msg.sender)` and `lockedAmount[msg.sender]` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, call `depositLP(uint256 _lpAmount)`, and assert `this.balance(msg.sender)` equals `lockedAmount[msg.sender]` and that no account can withdraw more than it put in.
