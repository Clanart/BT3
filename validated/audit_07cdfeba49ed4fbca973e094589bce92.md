### Title
Stale Base Fee Used in `NewDynamicFeeChecker` Ante Handler — (`File: ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` captures `feemarketParams *feemarkettypes.Params` at construction time and uses it inside the returned closure to derive the base fee for every subsequent Cosmos SDK EIP-1559 transaction. Because `SetBaseFee` never mutates the struct in place — it reads from the KV store into a fresh struct, updates it, and writes it back — the pointer captured at ante-handler construction always reflects the initial (genesis) base fee, not the dynamically adjusted per-block value. Every Cosmos SDK transaction that carries `ExtensionOptionDynamicFeeTx` is therefore checked against a permanently stale base fee.

### Finding Description

`NewDynamicFeeChecker` is constructed once (at app startup via `evmd/ante/handler_options.go`) and returns a closure that captures `feemarketParams`:

```go
// ante/evm/fee_checker.go:42-56
func NewDynamicFeeChecker(
    ethCfg *params.ChainConfig,
    evmParams *types.Params,
    feemarketParams *feemarkettypes.Params,   // captured once
) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [1](#0-0) 

`types.GetBaseFee` reads directly from the captured struct, not from the live keeper:

```go
// x/evm/types/utils.go:244-254
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()   // reads captured struct
    ...
}
``` [2](#0-1) 

Every block, `BeginBlock` calls `CalculateBaseFee` and then `SetBaseFee`, which **creates a new `Params` struct** and writes it to the KV store — it never touches the struct pointed to by the captured pointer:

```go
// x/feemarket/keeper/params.go:72-78
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)                        // new struct from KV store
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)                   // written back to KV store
    ...
}
``` [3](#0-2) 

The live keeper path (`k.feeMarketKeeper.GetBaseFee(ctx)`) correctly reads from the KV store on every call:

```go
// x/evm/keeper/keeper.go:310-319
func (k Keeper) getBaseFee(ctx sdk.Context, london bool) *big.Int {
    ...
    baseFee := k.feeMarketKeeper.GetBaseFee(ctx)   // live KV store read
    ...
}
``` [4](#0-3) 

`NewDynamicFeeChecker` never uses this live path; it always uses the stale captured pointer.

### Impact Explanation

After the first `BeginBlock` adjusts the base fee upward (e.g., from 1 Gwei to 1.1 Gwei due to high block utilization), the ante handler still enforces 1 Gwei. Any Cosmos SDK transaction carrying `ExtensionOptionDynamicFeeTx` with a `feeCap` between 1 Gwei and 1.1 Gwei passes the check at line 86:

```go
// ante/evm/fee_checker.go:86-88
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
``` [5](#0-4) 

The effective fee is then computed and deducted using the stale base fee, so the sender pays less than the protocol requires. Over time, as the base fee drifts further from the genesis value, the gap widens and the mis-accounting grows. Conversely, if the base fee falls below the genesis value, valid transactions are incorrectly rejected.

This matches the allowed High impact: **fee market ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted**.

### Likelihood Explanation

The entry path is fully unprivileged: any user submitting a Cosmos SDK transaction with `ExtensionOptionDynamicFeeTx` and a `feeCap` above the stale genesis base fee but below the current live base fee triggers the mis-accounting. No special role or key is required. The condition is reachable on any live chain where the base fee has moved from its genesis value, which is the normal operating state.

### Recommendation

Replace the captured-params lookup with a live keeper read inside the closure. The `NewDynamicFeeChecker` should accept a `FeeMarketKeeper` interface (or equivalent) and call `keeper.GetBaseFee(ctx)` at transaction time, mirroring the pattern already used in `x/evm/keeper/keeper.go`:

```go
// Instead of:
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)

// Use:
baseFee := feeMarketKeeper.GetBaseFee(ctx)
if baseFee == nil {
    baseFee = new(big.Int)
}
if !types.IsLondon(ethCfg, ctx.BlockHeight()) {
    baseFee = nil
}
```

### Proof of Concept

1. Chain starts with genesis base fee = 1 Gwei (`params.InitialBaseFee`). `NewDynamicFeeChecker` is constructed once, capturing `feemarketParams` with `BaseFee = 1 Gwei`.
2. After several high-utilization blocks, `BeginBlock` → `CalculateBaseFee` → `SetBaseFee` raises the live base fee to 2 Gwei in the KV store. The captured struct still holds 1 Gwei.
3. Attacker submits a Cosmos SDK tx with `ExtensionOptionDynamicFeeTx`, `MaxPriorityPrice = 0`, and `fees = 1.5 Gwei * gasLimit`.
4. `NewDynamicFeeChecker` computes `feeCap = 1.5 Gwei`, reads stale `baseFeeInt = 1 Gwei`, passes the `feeCap.LT(baseFeeInt)` check, and deducts `effectiveFee = 1.5 Gwei * gasLimit` — 25 % below the protocol-required 2 Gwei.
5. The transaction is committed with under-paid fees; the fee collector receives less than the current base fee mandates. [6](#0-5) [7](#0-6)

### Citations

**File:** ante/evm/fee_checker.go (L42-99)
```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
	return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
		feeTx, ok := tx.(sdk.FeeTx)
		if !ok {
			return nil, 0, fmt.Errorf("tx must be a FeeTx")
		}

		if ctx.BlockHeight() == 0 {
			// genesis transactions: fallback to min-gas-price logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}

		denom := evmParams.EvmDenom

		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}

		// default to `MaxInt64` when there's no extension option.
		maxPriorityPrice := sdkmath.NewInt(math.MaxInt64)

		// get the priority tip cap from the extension option.
		if hasExtOptsTx, ok := tx.(authante.HasExtensionOptionsTx); ok {
			for _, opt := range hasExtOptsTx.GetExtensionOptions() {
				if extOpt, ok := opt.GetCachedValue().(*ethermint.ExtensionOptionDynamicFeeTx); ok {
					maxPriorityPrice = extOpt.MaxPriorityPrice
					break
				}
			}
		}

		if maxPriorityPrice.Sign() == -1 {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "priority fee is negative")
		}

		gas := feeTx.GetGas()
		feeCoins := feeTx.GetFee()
		fee := feeCoins.AmountOf(denom)

		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}

		// calculate the effective gas price using the EIP-1559 logic.
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

		// NOTE: create a new coins slice without having to validate the denom
		effectiveFee := sdk.Coins{
			{
				Denom:  denom,
				Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),
			},
		}
```

**File:** x/evm/types/utils.go (L244-254)
```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
	if !IsLondon(ethCfg, height) {
		return nil
	}
	baseFee := feemarketParams.GetBaseFee()
	// should not be nil if london hardfork enabled
	if baseFee == nil {
		return new(big.Int)
	}
	return baseFee
}
```

**File:** x/feemarket/keeper/params.go (L72-78)
```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
	params := k.GetParams(ctx)
	params.BaseFee = ethermint.SaturatedNewInt(baseFee)
	err := k.SetParams(ctx, params)
	if err != nil {
		return
	}
```

**File:** x/evm/keeper/keeper.go (L310-319)
```go
func (k Keeper) getBaseFee(ctx sdk.Context, london bool) *big.Int {
	if !london {
		return nil
	}
	baseFee := k.feeMarketKeeper.GetBaseFee(ctx)
	if baseFee == nil {
		// return 0 if feemarket not enabled.
		baseFee = big.NewInt(0)
	}
	return baseFee
```

**File:** x/feemarket/keeper/abci.go (L30-51)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

	// return immediately if base fee is nil
	if baseFee == nil {
		return nil
	}

	k.SetBaseFee(ctx, baseFee)

	defer func() {
		telemetry.SetGauge(float32(baseFee.Int64()), "feemarket", "base_fee") //nolint:staticcheck
	}()

	// Store current base fee in event
	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeFeeMarket,
			sdk.NewAttribute(types.AttributeKeyBaseFee, baseFee.String()),
		),
	})
	return nil
```
