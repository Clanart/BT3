# Q5525: WombatPoolHelperV2.depositNative - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Starting from a state where the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged EOA use `depositNative(uint256 _minimumLiquidity)` to leave `_liquidity burned via burnReceiptToken` inconsistent with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violating the invariant that a helper must revalidate the pool identity it acts on before moving value and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the receipt token is minted to the helper while the credit is directed at a different address, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that a helper must revalidate the pool identity it acts on before moving value.
