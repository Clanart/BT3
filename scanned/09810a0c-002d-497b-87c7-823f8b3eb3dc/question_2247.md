# Q2247: WombatStaking.withdraw - withdraw pays out a balance delta rather than a computed entitlement

## Question
In wombat/WombatStaking.sol, withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, and drive `IERC20(wom).balanceOf(address(this))` out of agreement with `totalConverted in mWOM` - breaking the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM`, run the attacker's `withdraw(address,uint256,uint256,address) via a pool helper` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
