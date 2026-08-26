# Q3290: WombatStaking.deposit - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker reach this through `deposit(address,uint256,uint256,address,address) via a pool helper` while the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, and drive `totalAccumulated in mWOM` out of agreement with `veWom balance of WombatStaking` - breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `deposit(address,uint256,uint256,address,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(address,uint256,uint256,address,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minimumLiquidity and _for, forwarded verbatim by WombatPoolHelper/V2/AnkrBNBPoolHelper
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, call `deposit(address,uint256,uint256,address,address) via a pool helper`, and assert `totalAccumulated in mWOM` equals `veWom balance of WombatStaking` and that no account can withdraw more than it put in.
