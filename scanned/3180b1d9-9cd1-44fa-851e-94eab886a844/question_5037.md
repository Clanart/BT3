# Q5037: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
wombat/WombatPoolHelperV2.sol: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Under the attacker has moved the wom/mWom Wombat pool immediately before calling, is there an unprivileged sequence of `depositLP(uint256 _lpAmount)` that leaves `pid cached at construction` unreconciled with `pools[lpToken].pid in WombatStaking`, violates the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has moved the wom/mWom Wombat pool immediately before calling, have the attacker run `depositLP(uint256 _lpAmount)`, then assert the victim's claimable value and the `pid cached at construction` versus `pools[lpToken].pid in WombatStaking` relation are unchanged by the attacker's transaction.
