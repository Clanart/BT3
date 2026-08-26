# Q2320: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
Note that in wombat/WombatPoolHelperV2.sol, withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 _liquidity, uint256 _minAmount)` under the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction and force `IERC20(stakingToken).totalSupply()` apart from `the MasterWombat staked balance for pid`, breaking the invariant that receipt supply must fall in the same step as the backing it represents for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _minimumLiquidity to zero and sandwiches the Wombat pool in the same transaction, snapshot `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
