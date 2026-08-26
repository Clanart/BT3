# Q5635: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
In wombat/WombatPoolHelperV2.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Does `depositLP(uint256 _lpAmount)` let an unprivileged caller exploit that under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `depositLP(uint256 _lpAmount)` sequence atomically under MasterMagpie is paused so depositFor reverts while the Wombat leg has already executed, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
