# Q1742: WombatPoolHelper.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
wombat/WombatPoolHelper.sol - the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Can an unprivileged attacker controlling _liquidity and _minAmount, with the payout measured as a balance delta, under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` and the invariant that a helper must revalidate the pool identity it acts on before moving value, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence atomically under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
