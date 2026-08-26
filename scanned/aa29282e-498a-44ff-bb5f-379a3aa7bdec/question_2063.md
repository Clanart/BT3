# Q2063: WombatStaking.deposit - withdraw pays out a balance delta rather than a computed entitlement

## Question
wombat/WombatStaking.sol: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `totalAccumulated in mWOM` unreconciled with `veWom balance of WombatStaking`, violates the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, have the attacker run `deposit(address,uint256,uint256,address,address) via a pool helper`, then assert the victim's claimable value and the `totalAccumulated in mWOM` versus `veWom balance of WombatStaking` relation are unchanged by the attacker's transaction.
