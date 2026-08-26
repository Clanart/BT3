# Q2252: AnkrBNBPoolHelper.withdraw - deposit and withdraw both run the full harvest and fee path

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 _liquidity, uint256 _minAmount)` under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction and force `_liquidity burned via burnReceiptToken` apart from `the deposit-token balance delta paid out by WombatStaking.withdraw`, breaking the invariant that principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: deposit and withdraw both run the full harvest and fee path)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: WombatStaking._toMasterWomAndSendReward is invoked on every deposit, depositLP and withdraw, so any revert inside the fee loop, the smart convert leg or a rewarder queue blocks principal movement for the whole pool. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: principal deposits and withdrawals must not depend on an external price or an optional reward leg succeeding; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, snapshot `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
