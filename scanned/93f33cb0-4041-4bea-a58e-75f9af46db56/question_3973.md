# Q3973: mWOM.convert - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. With _amount, and the block relative to any pending convertAllWom under attacker control and helper is unset so convertAndStake reverts and only the plain mint path is reachable, can an unprivileged caller sequence `convert(uint256 _amount)` so that `totalConverted` and `totalAccumulated` no longer reconcile, violating the invariant that wrapper supply must never exceed the backing actually secured for it and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is unset so convertAndStake reverts and only the plain mint path is reachable, call `convert(uint256 _amount)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
