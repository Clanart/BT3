# Q0676: ArbWomUp.incentiveDeposit - the reward is capped at the balance but the claimed counter records the capped figure

## Question
In wombat/ArbWomUp.sol, getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `rewardTier[i]` inconsistent with `rewardMultiplier[i-1]`, violating the invariant that a cap applied because the contract is temporarily short must not permanently destroy the entitlement and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is capped at the balance but the claimed counter records the capped figure)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: a cap applied because the contract is temporarily short must not permanently destroy the entitlement; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated) under the caller splits the same total deposit across many small calls, asserting on every row that a cap applied because the contract is temporarily short must not permanently destroy the entitlement.
