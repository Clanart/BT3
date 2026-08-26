# Q0459: ArbWomUp.incentiveDeposit - the reward is capped at the balance but the claimed counter records the capped figure

## Question
Consider wombat/ArbWomUp.sol, where getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `usdtReward` and `IERC20(usdt).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that a cap applied because the contract is temporarily short must not permanently destroy the entitlement and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the reward is capped at the balance but the claimed counter records the capped figure)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() returns min(usdtReward, usdtleft) and incentiveDeposit then writes claimedReward[msg.sender] += rewardToSend, so a deposit made while the contract is underfunded permanently records less than the tier earned and the difference can never be recovered. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: a cap applied because the contract is temporarily short must not permanently destroy the entitlement; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller sizes _amount to cross several tier boundaries at once, have the attacker run `incentiveDeposit(uint256 _amount)`, then assert the victim's claimable value and the `usdtReward` versus `IERC20(usdt).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
