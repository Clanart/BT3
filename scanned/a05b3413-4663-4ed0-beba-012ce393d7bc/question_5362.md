# Q5362: WombatStaking.deposit - withdraw pays out a balance delta rather than a computed entitlement

## Question
Consider wombat/WombatStaking.sol, where withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Assuming the bonus reward token registered for the asset is also one of the fee currencies, can an unprivileged attacker turn this into a divergence between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` via `deposit(address,uint256,uint256,address,address) via a pool helper`, breaking the invariant that a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: withdraw pays out a balance delta rather than a computed entitlement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: withdraw() transfers IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw to _sender, so any deposit token that arrives at the contract during the Wombat withdrawal, from a fee split, a donation or a re-entrant path, is paid to the withdrawing caller. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: a withdrawal must pay the entitlement derived from the burned receipt tokens, not whatever balance appeared during the call; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bonus reward token registered for the asset is also one of the fee currencies, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `totalAccumulated in mWOM` equals `veWom balance of WombatStaking` and that no account can withdraw more than it put in.
