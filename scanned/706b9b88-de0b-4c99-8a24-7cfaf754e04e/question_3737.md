# Q3737: mWOM.deposit - deposit mints mWOM without locking the WOM

## Question
Note that in wombat/mWOM.sol, the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount)` under helper is set to a SimplePoolHelper and the attacker uses convertAndStake and force `totalConverted` apart from `totalAccumulated`, breaking the invariant that every mWOM in circulation must be backed by WOM the protocol has actually committed for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: deposit mints mWOM without locking the WOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the deposit() variant calls _convert(_amount, false, false), which keeps the WOM inside the mWOM contract and never calls convertWOM, so circulating mWOM is backed by idle WOM rather than by veWOM and totalAccumulated does not move. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: every mWOM in circulation must be backed by WOM the protocol has actually committed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is set to a SimplePoolHelper and the attacker uses convertAndStake, call `deposit(uint256 _amount)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
