### Title
Stale `feemarketParams` Snapshot in `newCosmosAnteHandler` Causes Incorrect Base Fee Validation for Cosmos Transactions - (File: `evmd/ante/handler_options.go`)

### Summary

`newCosmosAnteHandler` captures a one-time snapshot of `feemarketParams` at construction time and passes a pointer to it into `NewDynamicFeeChecker` and `NewMinGasPriceDecorator`. Because `feemarketParams.BaseFee` is updated every block by `BeginBlock`, the snapshot becomes stale immediately after the first block. Cosmos transactions using the `ExtensionOptionDynamicFeeTx` extension are then validated against the stale base fee, allowing under-priced transactions to pass the ante handler and commit with mis-accounted fees.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` fetches `feemarketParams` once at construction time:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // ← one-time snapshot
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

The pointer `&feemarketParams` is captured by the returned `sdk.AnteHandler` closure. Inside `NewDynamicFeeChecker`, every transaction evaluation calls:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`feemarketParams` here is the stale construction-time snapshot, never refreshed. The base fee is stored in `params.BaseFee` and is updated every block by `feemarket.BeginBlock` → `SetBaseFee`:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [3](#0-2) 

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [4](#0-3) 

The stale `feemarketParams.BaseFee` is then used to compute `effectiveFee` that is returned to `NewDeductFeeDecorator`:

```go
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
effectiveFee := sdk.Coins{{Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))}}
return effectiveFee, priority, nil
``` [5](#0-4) 

By contrast, `newEthAnteHandler` (for EVM transactions) correctly fetches live params on every invocation:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
feemarketParams := &blockCfg.FeeMarketParams
baseFee := blockCfg.BaseFee
``` [6](#0-5) 

The same stale-snapshot pattern also affects `MinGasPriceDecorator`, which reads `mpd.feemarketParams.MinGasPrice` directly from the frozen pointer:

```go
minGasPrice := mpd.feemarketParams.MinGasPrice
``` [7](#0-6) 

### Impact Explanation

After the first block, `feemarketParams.BaseFee` in the Cosmos ante handler diverges from the live on-chain base fee. When blocks are consistently above the gas target, `CalculateBaseFee` raises the base fee each block. Any Cosmos transaction carrying `ExtensionOptionDynamicFeeTx` with a `feeCap` between the stale (lower) base fee and the actual (higher) base fee will:

1. Pass the `feeCap >= baseFee` check using the stale value.
2. Have its `effectiveFee` computed from the stale base fee, so the amount deducted by `NewDeductFeeDecorator` is lower than the protocol-required base fee.

This is a fee market / ante handler bug that permits valid user funds/fees to be mis-accounted — matching the **High** allowed impact: *"fee market, ante handler … bug that permits … valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The base fee is recalculated and written to the KV store on every block's `BeginBlock`. The snapshot captured at app startup is therefore stale from block 2 onward. No special conditions, governance actions, or privileged access are required; any unprivileged user submitting a Cosmos transaction with `ExtensionOptionDynamicFeeTx` after the first block triggers the discrepancy. The divergence grows monotonically when the chain is under load.

### Recommendation

Fetch `feemarketParams` (and specifically `BaseFee`) fresh inside the `NewDynamicFeeChecker` closure by accepting a `FeeMarketKeeper` interface and calling `GetParams(ctx)` per invocation, mirroring the pattern already used in `newEthAnteHandler` via `EVMBlockConfig`. The `MinGasPriceDecorator` already holds a `feesKeeper` reference but never uses it to refresh params; it should do so as well.

### Proof of Concept

1. Node starts; `newCosmosAnteHandler` is called once. Snapshot: `feemarketParams.BaseFee = 1_000_000_000`.
2. Blocks 1–N are full (gas used > gas target). `BeginBlock` raises the live base fee to, say, `1_200_000_000`.
3. Attacker submits a Cosmos `MsgSend` with `ExtensionOptionDynamicFeeTx` and `feeCap = 1_100_000_000` (above stale base fee, below live base fee).
4. `NewDynamicFeeChecker` evaluates `feeCap (1.1e9) >= baseFee (1.0e9, stale)` → passes.
5. `effectivePrice = min(1.0e9 + tip, 1.1e9)` is computed from the stale base fee; `NewDeductFeeDecorator` deducts this under-priced amount.
6. Transaction commits; the attacker paid `~1.1e9 * gas` instead of the required `~1.2e9 * gas`, with the difference never collected.

### Citations

**File:** evmd/ante/handler_options.go (L88-96)
```go
		blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
		if err != nil {
			return ctx, errorsmod.Wrap(errortypes.ErrLogic, err.Error())
		}
		evmParams := &blockCfg.Params
		evmDenom := evmParams.EvmDenom
		feemarketParams := &blockCfg.FeeMarketParams
		baseFee := blockCfg.BaseFee
		rules := blockCfg.Rules
```

**File:** evmd/ante/handler_options.go (L178-198)
```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker ante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
	decorators := make([]sdk.AnteDecorator, 0, 16+len(extra))
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
		ante.NewSetUpContextDecorator(),
		ante.NewExtensionOptionsDecorator(options.ExtensionOptionChecker),
		ante.NewValidateBasicDecorator(),
		ante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
```

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

**File:** ante/evm/fee_checker.go (L91-109)
```go
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

		// NOTE: create a new coins slice without having to validate the denom
		effectiveFee := sdk.Coins{
			{
				Denom:  denom,
				Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),
			},
		}

		bigPriority := effectivePrice.Sub(baseFeeInt).Quo(types.DefaultPriorityReduction)
		priority := int64(math.MaxInt64)

		if bigPriority.IsInt64() {
			priority = bigPriority.Int64()
		}

		return effectiveFee, priority, nil
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

**File:** ante/cosmos/min_gas_price.go (L54-58)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
```
