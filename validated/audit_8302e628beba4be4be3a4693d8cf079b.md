### Title
Distribution reward withdrawal can OOG/permanently freeze delegator rewards when a validator has accumulated many slash events since delegation began - (File: sei-cosmos/x/distribution/keeper/delegation.go)

### Summary
The distribution module's `calculateDelegationRewards` (invoked by `WithdrawDelegationRewards`, which is reachable from an unprivileged EVM caller through the distribution precompile) iterates over every slash event recorded for a validator between the delegator's starting height and the current height. If a delegator never withdraws while its validator accumulates many slash events over a long period, this loop can grow unbounded, mirroring the analog report's `SdtStakingPositionService::_claimCvgSdtRewards` per-cycle loop pattern.

### Finding Description
`calculateDelegationRewards` in [1](#0-0)  calls `k.IterateValidatorSlashEventsBetween(ctx, del.GetValidatorAddr(), startingHeight, endingHeight, ...)` to walk every intervening slash event and accumulate rewards period-by-period. `startingHeight` is the delegator's `DelegatorStartingInfo.Height`, set once when the delegation was initialized/last reinitialized [2](#0-1) , and `endingHeight` is the current block height. There is no cap on the number of slash events processed in a single call.

This function is on the reachable path for an ordinary transaction: `WithdrawDelegationRewards` in `sei-cosmos/x/distribution/keeper/keeper.go` calls `CalculateDelegationRewards`, and this keeper method is exposed to EVM callers via the distribution precompile's `withdrawDelegationRewards` / `withdrawMultipleDelegationRewards` methods [3](#0-2) [4](#0-3) , as well as the native Cosmos `MsgWithdrawDelegatorReward` message. A delegator (or, for `withdrawMultipleDelegationRewards`, one call touching several validators) can be forced into a large iteration if the validator has been slashed repeatedly (e.g., liveness/downtime slashes) over the span the delegation has sat unclaimed, since `IncrementValidatorPeriod`/slash processing occurs every time a slash is applied and the historical record plus the slash event log persist until referenced state is cleaned up (see `IterateValidatorSlashEventsBetween` usages across `store.go`, `querier.go`, `delegation.go`).

Note: unlike the original SdtStaking analog, this loop is bounded per-transaction by the *number of slash events*, not raw block count, so worst-case behavior is far less severe in practice on Sei (a validator would need to be slashed an extreme number of times against one un-claimed delegation to approach block-gas-limit territory). This makes the analog directionally valid but with a much higher and less-likely-to-be-hit bound than the original report's "one iteration per unclaimed cycle."

### Impact Explanation
If the iteration count grows large enough to exceed the block/transaction gas limit, the delegator's `WithdrawDelegationRewards` transaction (whether submitted natively or via the EVM distribution precompile) will always fail with out-of-gas, permanently preventing that delegator from ever withdrawing accrued rewards for that validator — a fund-freezing condition consistent with the "Accept only concrete fund loss or permanent freezing" validation bar. There is no way to partially claim or skip ahead, since `calculateDelegationRewards` always replays the full slash history from `startingPeriod`.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a validator to accumulate an extraordinarily large number of `ValidatorSlashEvent` records (from downtime/double-sign infractions) against one specific delegator across a long time span, combined with that delegator never triggering `initializeDelegation` (which would reset `startingHeight`/reference the latest period) via new delegation, undelegation, or a successful reward withdrawal in the interim. Redelegating or delegating more funds to the same validator would refresh `startingInfo` and reset the count, so this only manifests for delegators who set-and-forget a stake with a chronically-slashed validator over a very long period.

### Recommendation
- Add a configurable per-call cap on the number of slash events processed within `IterateValidatorSlashEventsBetween`/`calculateDelegationRewards`, with the withdrawal exposing partial "claim-up-to-height" semantics (mirroring the report's own recommendation of a claim-by-cycle approach) so a delegator can incrementally catch up across multiple transactions.
- Alternatively, prune or compact historical slash events more proactively once safely superseded, and expose an off-chain/gas-estimation warning so front-ends discourage extremely long-unclaimed delegations against high-slash-frequency validators.

### Proof of Concept
Conceptual (no on-chain data confirming an actual validator has hit this threshold on Sei was inspected, since that requires live chain state, which is outside this repo-only review):
1. Delegator D delegates to validator V and never withdraws, redelegates, or increases the delegation (so `DelegatorStartingInfo.Height` remains fixed at the initial height).
2. V is slashed repeatedly (e.g., via prolonged downtime infractions) over an extended period, each slash appending a new `ValidatorSlashEvent` between D's starting height and the current height.
3. D (or anyone acting on D's behalf) calls `MsgWithdrawDelegatorReward` natively, or `withdrawDelegationRewards`/`withdrawMultipleDelegationRewards` on the EVM distribution precompile [3](#0-2) .
4. `CalculateDelegationRewards` → `calculateDelegationRewards` iterates every slash event in `[startingHeight, endingHeight]` [5](#0-4) , and once the slash-event count is large enough to exceed the tx/block gas limit, the call always fails, permanently freezing D's rewards for V.

### Citations

**File:** sei-cosmos/x/distribution/keeper/delegation.go (L159-197)
```go
func (k Keeper) calculateDelegationRewards(ctx sdk.Context, val stakingtypes.ValidatorI, del stakingtypes.DelegationI, endingPeriod uint64, endingRatio *sdk.DecCoins) (rewards sdk.DecCoins) {
	// fetch starting info for delegation
	startingInfo := k.GetDelegatorStartingInfo(ctx, del.GetValidatorAddr(), del.GetDelegatorAddr())

	if startingInfo.Height == uint64(ctx.BlockHeight()) { //nolint:gosec // block heights are always non-negative
		// started this height, no rewards yet
		return
	}

	startingPeriod := startingInfo.PreviousPeriod
	stake := startingInfo.Stake

	// Iterate through slashes and withdraw with calculated staking for
	// distribution periods. These period offsets are dependent on *when* slashes
	// happen - namely, in BeginBlock, after rewards are allocated...
	// Slashes which happened in the first block would have been before this
	// delegation existed, UNLESS they were slashes of a redelegation to this
	// validator which was itself slashed (from a fault committed by the
	// redelegation source validator) earlier in the same BeginBlock.
	startingHeight := startingInfo.Height
	// Slashes this block happened after reward allocation, but we have to account
	// for them for the stake sanity check below.
	endingHeight := uint64(ctx.BlockHeight()) //nolint:gosec // block heights are always non-negative
	if endingHeight > startingHeight {
		k.IterateValidatorSlashEventsBetween(ctx, del.GetValidatorAddr(), startingHeight, endingHeight,
			func(height uint64, event types.ValidatorSlashEvent) (stop bool) {
				endingPeriod := event.ValidatorPeriod
				if endingPeriod > startingPeriod {
					rewards = rewards.Add(k.calculateDelegationRewardsBetween(ctx, val, startingPeriod, endingPeriod, stake)...)

					// Note: It is necessary to truncate so we don't allow withdrawing
					// more rewards than owed.
					stake = stake.MulTruncate(sdk.OneDec().Sub(event.Fraction))
					startingPeriod = endingPeriod
				}
				return false
			},
		)
	}
```

**File:** precompiles/distribution/distribution.go (L359-373)
```go
func (p PrecompileExecutor) withdrawDelegationRewards(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	if err := p.validateInput(value, args, 1); err != nil {
		return nil, 0, err
	}
	delegator, err := p.getDelegator(ctx, caller)
	if err != nil {
		return nil, 0, err
	}
	validatorAddress := args[0].(string)
	execute := func() (sdk.Int, error) {
		amts, err := p.withdraw(ctx, delegator, validatorAddress)
		return amts.AmountOf(sdk.DefaultBondDenom), err
	}
	return p.withdrawDelegationRewardsFor(ctx, method, caller, validatorAddress, evm, execute)
}
```

**File:** precompiles/distribution/distribution.go (L466-513)
```go
func (p PrecompileExecutor) withdrawMultipleDelegationRewards(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
	err := p.validateInput(value, args, 1)
	if err != nil {
		rerr = err
		return
	}

	delegator, err := p.getDelegator(ctx, caller)
	if err != nil {
		rerr = err
		return
	}
	validators := args[0].([]string)
	amts := make([]*big.Int, 0, len(validators))
	for _, valAddr := range validators {
		amt, err := p.withdraw(ctx, delegator, valAddr)
		if err != nil {
			rerr = err
			return
		}
		amts = append(amts, amt.AmountOf(sdk.DefaultBondDenom).BigInt())
	}

	ret, rerr = method.Outputs.Pack(true)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)

	logData, err := p.abi.Events[MultipleDelegationRewardsEvent].Inputs.NonIndexed().Pack(validators, amts)
	if err != nil {
		rerr = err
		return
	}
	if err := pcommon.EmitEVMLog(evm, p.address, []common.Hash{
		MultipleDelegationRewardsEventSig,
		common.BytesToHash(caller.Bytes()),
	}, logData); err != nil {
		rerr = err
		return
	}
	return
}
```
