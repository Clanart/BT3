# Q1313: ArbWomUp.incentiveDeposit - the reward is capped at the balance but the claimed counter records the capped figure

## Question
In wombat/ArbWomUp.sol, getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the caller has already claimed most of their tier entitlement, and drive `rewardAmount / DENOMINATOR` out of agreement with `claimedReward[account]` - breaking the invariant that a cap applied because the contract is temporarily short must not permanently destroy the entitlement - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is capped at the balance but the claimed counter records the capped figure)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: a cap applied because the contract is temporarily short must not permanently destroy the entitlement; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the caller has already claimed most of their tier entitlement, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that a cap applied because the contract is temporarily short must not permanently destroy the entitlement.
