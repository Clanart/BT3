# Q3641: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Note that in wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under helper is set to a SimplePoolHelper and the attacker uses convertAndStake and force `totalConverted` apart from `totalAccumulated`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `convertAndStake(uint256 _amount)` sequence atomically under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting at the end that `totalConverted` still equals `totalAccumulated` and the PoC's balance delta is non-positive.
