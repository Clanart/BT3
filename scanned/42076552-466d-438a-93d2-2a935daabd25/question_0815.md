# Q0815: mWOM.convert - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Starting from a state where rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged EOA use `convert(uint256 _amount)` to leave `totalConverted` inconsistent with `totalAccumulated`, violating the invariant that wrapper supply must never exceed the backing actually secured for it and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, call `convert(uint256 _amount)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
