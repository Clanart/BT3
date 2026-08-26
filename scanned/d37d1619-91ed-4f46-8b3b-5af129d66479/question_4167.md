# Q4167: WombatStaking.deposit - withdraw pays out a balance delta rather than a computed entitlement

## Question
wombat/WombatStaking.sol: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Under several feeInfos entries are active at once and the harvested amount is small, is there an unprivileged sequence of `deposit(address,uint256,uint256,address,address) via a pool helper` that leaves `isPoolFeeFree[_lpToken]` unreconciled with `feeInfos.length`, violates the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: several feeInfos entries are active at once and the harvested amount is small.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper) under several feeInfos entries are active at once and the harvested amount is small, asserting on every row that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call.
