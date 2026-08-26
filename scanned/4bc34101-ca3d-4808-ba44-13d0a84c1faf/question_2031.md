# Q2031: WomUp.stake - stake and withdraw inside one block capture an interval

## Question
In wombat/WomUp.sol, stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Does `stake(uint256 _amount)` let an unprivileged caller exploit that under the attacker migrates and withdraws inside one transaction, so that `_balances[account]` diverges from `_totalSupply`, the invariant that reward share must require the stake to have been held across the interval it is paid for is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `stake(uint256 _amount)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the block, with the WOM immediately converted 1:1 into mWOM
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker migrates and withdraws inside one transaction, have the attacker run `stake(uint256 _amount)`, then assert the victim's claimable value and the `_balances[account]` versus `_totalSupply` relation are unchanged by the attacker's transaction.
