# Q2362: WombatStaking.withdraw - fee split truncation drains the residual

## Question
wombat/WombatStaking.sol - _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Can an unprivileged attacker controlling _liquidity and _minAmount, forwarded verbatim from the helper's withdraw, under a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, exploit this through `withdraw(address,uint256,uint256,address) via a pool helper` to break the reconciliation between `feeInfos[i].value` and `totalFee` and the invariant that every harvested unit must end up either in a fee destination or in the pool rewarder, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: fee split truncation drains the residual)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: _sendRewards() computes feeAmount = originalRewardAmount * feeInfo.value / DENOMINATOR per fee and subtracts each from _amount, so with several fees and a small reward the truncated residues never reach any rewarder and accumulate in the contract. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: every harvested unit must end up either in a fee destination or in the pool rewarder; concretely, `feeInfos[i].value` must stay reconciled with `totalFee`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `feeInfos[i].value` versus `totalFee` relation are unchanged by the attacker's transaction.
