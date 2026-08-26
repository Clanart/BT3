# Q5558: WombatPoolHelperV2.withdraw - pid, lpToken and stakingToken are fixed at construction and never revalidated

## Question
In wombat/WombatPoolHelperV2.sol, the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under the receipt token is minted to the helper while the credit is directed at a different address, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that a helper must revalidate the pool identity it acts on before moving value is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: pid, lpToken and stakingToken are fixed at construction and never revalidated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: the helper caches pid, lpToken, stakingToken and rewarder at construction and forwards them without checking they still match the Pool struct WombatStaking holds, so a pool that was re-registered or removed leaves the helper acting on a stale identity. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must revalidate the pool identity it acts on before moving value; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the receipt token is minted to the helper while the credit is directed at a different address, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
