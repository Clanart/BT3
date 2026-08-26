# Q5320: WombatStaking.harvest - harvest routes protocol WOM through a spot-priced smart convert

## Question
wombat/WombatStaking.sol - _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Can an unprivileged attacker controlling _lpToken and the timing of every harvest-driven fee split, under the bonus reward token registered for the asset is also one of the fee currencies, exploit this through `harvest(address _lpToken)` to break the reconciliation between `isPoolFeeFree[_lpToken]` and `feeInfos.length` and the invariant that protocol-owned value must not be traded at a price a caller can set in the same transaction, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatStaking.sol -> `harvest(address _lpToken)` (mechanism: harvest routes protocol WOM through a spot-priced smart convert)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest(address _lpToken)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpToken and the timing of every harvest-driven fee split
- Exploit idea: _sendRewards() sends the mWOM-flagged fee leg through IConverter(smartWomConverter).smartConvert(feeAmount, 0), and SmartWomConvert prices that swap from the live Wombat pool via currentRatio() and maxSwapAmount(), so an attacker who moves that pool immediately before calling harvest sets the price the protocol trades at. Precondition: the bonus reward token registered for the asset is also one of the fee currencies.
- Invariant to test: protocol-owned value must not be traded at a price a caller can set in the same transaction; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_lpToken and the timing of every harvest-driven fee split) under the bonus reward token registered for the asset is also one of the fee currencies, asserting on every row that protocol-owned value must not be traded at a price a caller can set in the same transaction.
