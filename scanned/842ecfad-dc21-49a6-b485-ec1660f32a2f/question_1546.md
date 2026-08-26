# Q1546: BribeRewardPool.withdrawFor - balanceOf override diverges from the inherited totalStaked semantics

## Question
In rewards/BribeRewardPool.sol, BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Starting from a state where totalSupply is zero because every voter has unvoted, can an unprivileged EOA use `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that all reward math in a contract must read the balance ledger the contract actually maintains and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: balanceOf override diverges from the inherited totalStaked semantics)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: BribeRewardPool overrides balanceOf and totalStaked to read its private _balances and totalSupply, while the inherited reward math was written against a MasterMagpie-backed ledger, so any inherited path that still assumes the operator ledger reads the wrong source. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: all reward math in a contract must read the balance ledger the contract actually maintains; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that totalSupply is zero because every voter has unvoted, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that all reward math in a contract must read the balance ledger the contract actually maintains.
