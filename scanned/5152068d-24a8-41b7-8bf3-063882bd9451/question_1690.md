# Q1690: WombatStaking.withdraw - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol - _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under the contract is holding WOM collected as a protocol fee that has not yet been split, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted in mWOM` and the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(address,uint256,uint256,address) via a pool helper` sequence atomically under the contract is holding WOM collected as a protocol fee that has not yet been split, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted in mWOM` and the PoC's balance delta is non-positive.
