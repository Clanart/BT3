# Q4648: WombatPoolHelperV2.depositFor - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelperV2.sol - the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker controlling _for (any address) and _amount, with _minimumLiquidity hardcoded to zero, under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, exploit this through `depositFor(uint256 _amount, address _for)` to break the reconciliation between `this.balance(msg.sender)` and `lockedAmount[msg.sender]` and the invariant that a helper must revalidate the pool identity it acts on before moving value, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for)` sequence atomically under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
