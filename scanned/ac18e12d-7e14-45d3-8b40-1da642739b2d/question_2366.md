# Q2366: WombatPoolHelperV2.withdraw - deposit and withdraw both run the full harvest and fee path

## Question
wombat/WombatPoolHelperV2.sol: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. With _liquidity and _minAmount under attacker control and the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, can an unprivileged caller sequence `withdraw(uint256 _liquidity, uint256 _minAmount)` so that `this.balance(msg.sender)` and `lockedAmount[msg.sender]` no longer reconcile, violating the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding and realising High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, fuzz the attacker inputs (_liquidity and _minAmount), and assert after every call that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding.
