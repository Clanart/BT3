# Q2772: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. With _amount and the helper routing that stakes the freshly minted mWOM under attacker control and the attacker calls convertAllWom on WombatStaking in the same transaction, can an unprivileged caller sequence `convertAndStake(uint256 _amount)` so that `rewardRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that wrapper supply must never exceed the backing actually secured for it and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls convertAllWom on WombatStaking in the same transaction, call `convertAndStake(uint256 _amount)`, and assert `rewardRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.
