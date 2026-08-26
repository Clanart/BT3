# Q4938: WombatPoolHelperV2.deposit - deposit and withdraw both run the full harvest and fee path

## Question
wombat/WombatPoolHelperV2.sol: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. With _amount and _minimumLiquidity under attacker control and the attacker has moved the wom/mWom Wombat pool immediately before calling, can an unprivileged caller sequence `deposit(uint256 _amount, uint256 _minimumLiquidity)` so that `_minimumLiquidity supplied by the caller` and `the LP actually minted by the Wombat pool` no longer reconcile, violating the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the attacker has moved the wom/mWom Wombat pool immediately before calling.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the attacker has moved the wom/mWom Wombat pool immediately before calling, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
