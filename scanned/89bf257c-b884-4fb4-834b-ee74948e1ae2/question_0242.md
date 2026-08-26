# Q0242: ArbWomUp.incentiveDeposit - the reward is capped at the balance but the claimed counter records the capped figure

## Question
wombat/ArbWomUp.sol: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the contract has just been topped up with USDT, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `rewardAmount / DENOMINATOR` and `claimedReward[account]` no longer reconcile, violating the invariant that a cap applied because the contract is temporarily short must not permanently destroy the entitlement and realising High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is capped at the balance but the claimed counter records the capped figure)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Precondition: the contract has just been topped up with USDT.
- Invariant to test: a cap applied because the contract is temporarily short must not permanently destroy the entitlement; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the contract has just been topped up with USDT, asserting at the end that `rewardAmount / DENOMINATOR` still equals `claimedReward[account]` and the PoC's balance delta is non-positive.
