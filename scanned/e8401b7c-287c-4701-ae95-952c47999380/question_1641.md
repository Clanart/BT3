# Q1641: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Note that in wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under an owner funding transfer of MGP is sitting in the mempool and force `_amount minted as mWOM` apart from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAndStake(uint256 _amount)`: constrain the setup so that an owner funding transfer of MGP is sitting in the mempool, fuzz the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM), and assert after every call that wrapper supply must never exceed the backing actually secured for it.
