# Q1593: WombatPoolHelperV2.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
wombat/WombatPoolHelperV2.sol: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `this.balance(msg.sender)` unreconciled with `lockedAmount[msg.sender]`, violates the invariant that receipt supply must fall in the same step as the backing it represents, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, snapshot `this.balance(msg.sender)` and `lockedAmount[msg.sender]`, run the attacker's `withdraw(uint256 _liquidity, uint256 _minAmount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
