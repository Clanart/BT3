# Q1110: ArbWomUp.incentiveDeposit - the reward is capped at the balance but the claimed counter records the capped figure

## Question
In wombat/ArbWomUp.sol, getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Does `incentiveDeposit(uint256 _amount)` let an unprivileged caller exploit that under userWOMDeposited is still zero for the caller, so that `claimedReward[account]` diverges from `userWOMDeposited[account]`, the invariant that a cap applied because the contract is temporarily short must not permanently destroy the entitlement is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is capped at the balance but the claimed counter records the capped figure)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a cap applied because the contract is temporarily short must not permanently destroy the entitlement; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange userWOMDeposited is still zero for the caller, call `incentiveDeposit(uint256 _amount)`, and assert `claimedReward[account]` equals `userWOMDeposited[account]` and that no account can withdraw more than it put in.
