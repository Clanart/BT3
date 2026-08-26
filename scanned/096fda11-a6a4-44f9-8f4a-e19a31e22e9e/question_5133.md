# Q5133: WombatPoolHelper.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
Note that in wombat/WombatPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under the attacker has moved the wom/mWom Wombat pool immediately before calling and force `_minimumLiquidity supplied by the caller` apart from `the LP actually minted by the Wombat pool`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount and the LP tokens pulled from the caller
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has moved the wom/mWom Wombat pool immediately before calling, then assert `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` end identical in both runs.
