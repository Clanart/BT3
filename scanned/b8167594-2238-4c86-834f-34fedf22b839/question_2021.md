# Q2021: WombatPoolHelperV2.depositFor - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelperV2.sol: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. With _for (any address) and _amount, with _minimumLiquidity hardcoded to zero under attacker control and the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged caller sequence `depositFor(uint256 _amount, address _for)` so that `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` no longer reconcile, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any address) and _amount, with _minimumLiquidity hardcoded to zero
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for)` sequence atomically under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, asserting at the end that `IERC20(stakingToken).totalSupply()` still equals `the MasterWombat staked balance for pid` and the PoC's balance delta is non-positive.
