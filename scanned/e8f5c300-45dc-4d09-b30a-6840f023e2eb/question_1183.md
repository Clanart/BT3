# Q1183: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
In wombat/WombatStaking.sol, _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Starting from a state where the contract is holding WOM collected as a protocol fee that has not yet been split, can an unprivileged EOA use `harvest(address _lpToken)` to leave `womRewards measured by balance delta` inconsistent with `the amount queued into poolInfo.rewarder`, violating the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the contract is holding WOM collected as a protocol fee that has not yet been split.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `womRewards measured by balance delta` must stay reconciled with `the amount queued into poolInfo.rewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `harvest(address _lpToken)` sequence atomically under the contract is holding WOM collected as a protocol fee that has not yet been split, asserting at the end that `womRewards measured by balance delta` still equals `the amount queued into poolInfo.rewarder` and the PoC's balance delta is non-positive.
