## Title
EVM `NextBaseFeePerGas` updates every block with no emitted event, hiding fee-market changes from on-chain consumers - (File: `x/evm/keeper/fee.go`)

### Summary
The Sei EVM module recomputes and persists the dynamic EIP-1559 base fee for the next block on every `EndBlock`, but the setter that performs this critical state mutation never emits a Cosmos SDK event. This is the same class of defect described in the external report: important state-changing functions exist, but no event is emitted for consumers who rely on the event log to observe changes to consensus-critical parameters.

### Finding Description
`AdjustDynamicBaseFeePerGas` computes the new base fee for the next block based on `blockGasUsed`, target gas, and the min/max/adjustment parameters, then calls `k.SetNextBaseFeePerGas(ctx, newBaseFee)` to persist it: [1](#0-0) 

`SetNextBaseFeePerGas` itself is a raw store write with no event emission at all: [2](#0-1) 

This is invoked unconditionally from the EVM module's `EndBlock`/deferred processing (`AdjustDynamicBaseFeePerGas` is called from `x/evm/keeper/abci.go`), meaning the base fee for every future block silently changes each block with zero on-chain trace of *why* or *by how much*. Compare this to the oracle module in the same codebase, which follows the correct pattern for exactly this kind of update — `SetBaseExchangeRateWithEvent` wraps the raw setter and emits `EventTypeExchangeRateUpdate` with the new value: [3](#0-2) 

The EVM module's `x/evm/types/events.go` defines only `address_associated`, `pointer_registered`, and `signer` event types — there is no event type at all for base-fee or fee-parameter changes: [4](#0-3) 

Additionally, the underlying parameters that drive this computation (`MinimumFeePerGas`, `MaximumFeePerGas`, `MaxDynamicBaseFeeUpwardAdjustment`, `MaxDynamicBaseFeeDownwardAdjustment`, `TargetGasUsedPerBlock`) are set via the generic `SetParams`/`Paramstore.SetParamSet` call, which also does not emit any dedicated event describing which fee parameter changed and to what value: [5](#0-4) 

### Impact Explanation
The EVM base fee is the single most important input to every EVM transaction's effective gas price and fee accounting on Sei. Because no event is emitted when it changes:
- Wallets, RPC clients, and gas-estimation tooling that build gas-price expectations from event streams (rather than polling state every block) can silently diverge from the true fee market, causing transactions to be underpriced/dropped or overpaying users.
- If the base fee is ever driven to an unexpected value — whether via a bug in the EIP-1559 adjustment formula, a mis-set fee parameter through governance/param changes, or unexpected `blockGasUsed`/gas-limit interaction — there is no auditable on-chain signal to detect the anomaly quickly, delaying incident response for a fee-accounting bug that affects all users transacting through the EVM.
- This mirrors the report's exploit logic: a quietly-changed important variable (there, `spotShock`; here, `NextBaseFeePerGas`) can cause harm to users (mispriced fees) that goes unnoticed because there's no event trail.

This is a data-validation/observability gap in the fee-accounting path rather than a direct fund-transfer bug, so severity depends on how monitoring tooling depends on events versus state queries.

### Likelihood Explanation
This code path executes on every single block (`EndBlock`/deferred processing calls `AdjustDynamicBaseFeePerGas` unconditionally), so the missing-event condition is always present, not a rare edge case. No privileged access or malicious input is required to trigger the "silent update" — it's simply an inherent property of the current code, and any anomaly (bug, mis-tuned param, or param-authority mistake) would go unlogged.

### Recommendation
- Emit a dedicated event (e.g. `base_fee_updated`) from `SetNextBaseFeePerGas` (or a new wrapper analogous to `SetBaseExchangeRateWithEvent`) including old/new base fee and block height.
- Emit parameter-change events from `SetParams` in `x/evm/keeper/params.go` for fee-related parameters (`MinimumFeePerGas`, `MaximumFeePerGas`, `MaxDynamicBaseFeeUpwardAdjustment`, `MaxDynamicBaseFeeDownwardAdjustment`, `TargetGasUsedPerBlock`), mirroring how other Cosmos SDK modules (and the oracle module in this repo) log parameter/state changes.
- Add test coverage asserting these events are present after `AdjustDynamicBaseFeePerGas` and `SetParams` calls, similar to the existing `TestRegisterPointer` pattern that checks for `EventTypePointerRegistered`.

### Proof of Concept
Not applicable in the traditional "exploit transaction" sense — the issue is demonstrated by code inspection: `SetNextBaseFeePerGas` (`x/evm/keeper/fee.go:91-98`) and `SetParams` (`x/evm/keeper/params.go:15-17`) contain no `ctx.EventManager().EmitEvent(...)` calls, and grepping `x/evm/types/events.go` confirms no base-fee/param-change event type exists in the module at all, unlike the oracle module's `EventTypeExchangeRateUpdate` pattern used for an analogous per-block state update.

### Citations

**File:** x/evm/keeper/fee.go (L45-59)
```go
	// Ensure the new base fee is not lower than the minimum fee
	if newBaseFee.LT(minimumFeePerGas) {
		newBaseFee = minimumFeePerGas
	}

	// Ensure the new base fee is not higher than the maximum fee
	if newBaseFee.GT(maximumFeePerGas) {
		newBaseFee = maximumFeePerGas
	}

	// Set the new base fee for the next height
	k.SetNextBaseFeePerGas(ctx, newBaseFee)

	return &newBaseFee
}
```

**File:** x/evm/keeper/fee.go (L91-98)
```go
func (k *Keeper) SetNextBaseFeePerGas(ctx sdk.Context, baseFeePerGas sdk.Dec) {
	store := ctx.KVStore(k.storeKey)
	bz, err := baseFeePerGas.MarshalJSON()
	if err != nil {
		panic(err)
	}
	store.Set(types.NextBaseFeePerGasPrefix, bz)
}
```

**File:** x/oracle/keeper/keeper.go (L93-101)
```go
func (k Keeper) SetBaseExchangeRateWithEvent(ctx sdk.Context, denom string, exchangeRate sdk.Dec) {
	k.SetBaseExchangeRate(ctx, denom, exchangeRate)
	ctx.EventManager().EmitEvent(
		sdk.NewEvent(types.EventTypeExchangeRateUpdate,
			sdk.NewAttribute(types.AttributeKeyDenom, denom),
			sdk.NewAttribute(types.AttributeKeyExchangeRate, exchangeRate.String()),
		),
	)
}
```

**File:** x/evm/types/events.go (L1-14)
```go
package types

const (
	EventTypeAddressAssociated = "address_associated"
	EventTypePointerRegistered = "pointer_registered"
	EventTypeSigner            = "signer"

	AttributeKeySeiAddress     = "sei_addr"
	AttributeKeyEvmAddress     = "evm_addr"
	AttributeKeyPointerType    = "pointer_type"
	AttributeKeyPointee        = "pointee"
	AttributeKeyPointerAddress = "pointer_address"
	AttributeKeyPointerVersion = "pointer_version"
)
```

**File:** x/evm/keeper/params.go (L15-17)
```go
func (k Keeper) SetParams(ctx sdk.Context, params types.Params) {
	k.Paramstore.SetParamSet(ctx, &params)
}
```
