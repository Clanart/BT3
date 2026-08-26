# Q3052: mWOM.incentiveDeposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Starting from a state where the attacker calls convertAllWom on WombatStaking in the same transaction, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `totalConverted` inconsistent with `totalAccumulated`, violating the invariant that wrapper supply must never exceed the backing actually secured for it and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls convertAllWom on WombatStaking in the same transaction, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
