# Q4033: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Note that in wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under helper is unset so convertAndStake reverts and only the plain mint path is reachable and force `IERC20(this).totalSupply()` apart from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish helper is unset so convertAndStake reverts and only the plain mint path is reachable, have the attacker run `convertAndStake(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(this).totalSupply()` versus `IERC20(wom).balanceOf(wombatStaking) + veWom backing` relation are unchanged by the attacker's transaction.
