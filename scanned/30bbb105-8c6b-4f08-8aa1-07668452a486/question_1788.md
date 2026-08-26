# Q1788: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
wombat/mWOM.sol: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and an owner funding transfer of MGP is sitting in the mempool, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` no longer reconcile, violating the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange an owner funding transfer of MGP is sitting in the mempool, call `deposit(uint256 _amount)`, and assert `_amount minted as mWOM` equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and that no account can withdraw more than it put in.
