# Q1824: WomUp.withdraw - stake and withdraw inside one block capture an interval

## Question
wombat/WomUp.sol: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Under the target helper leaves a non-zero allowance after depositFor, is there an unprivileged sequence of `withdraw(uint256 amount, bool claim)` that leaves `_balances[account]` unreconciled with `_totalSupply`, violates the invariant that reward share must require the stake to have been held across the interval it is paid for, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake and withdraw inside one block capture an interval)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() and withdraw() both run the updateReward modifier around an instantaneous balance read with no minimum holding period, so a flash-funded stake around a reward interval boundary captures emission with no exposure. Precondition: the target helper leaves a non-zero allowance after depositFor.
- Invariant to test: reward share must require the stake to have been held across the interval it is paid for; concretely, `_balances[account]` must stay reconciled with `_totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the target helper leaves a non-zero allowance after depositFor, snapshot `_balances[account]` and `_totalSupply`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
