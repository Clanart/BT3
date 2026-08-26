# Q4979: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker reach this through `convertAndStake(uint256 _amount)` while the attacker repeats the call across several addresses in the same block, and drive `rewardRatio` out of agreement with `DENOMINATOR` - breaking the invariant that wrapper supply must never exceed the backing actually secured for it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAndStake(uint256 _amount)`: constrain the setup so that the attacker repeats the call across several addresses in the same block, fuzz the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM), and assert after every call that wrapper supply must never exceed the backing actually secured for it.
