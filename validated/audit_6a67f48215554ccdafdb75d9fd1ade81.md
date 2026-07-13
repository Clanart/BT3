### Title
`NewDynamicFeeChecker` Uses Stale Cached `feemarketParams` for Cosmos Tx Base Fee Validation — (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` captures a pointer to `feemarketParams` at ante handler construction time. Because the fee market base fee is updated every block in `BeginBlock` (written to the KV store), but the captured params struct is never refreshed, Cosmos native transactions are validated against a stale base fee rather than the current block's base fee. This is the direct analog of the external report's stale-cached-rate pattern: a cached value is used for fee accounting instead of the dynamically updated current value.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads `feemarketParams` once from the keeper and passes `&feemarketParams` (a pointer to a local stack variable that escapes to the heap) into `NewDynamicFeeChecker`:

```go
// evmd/ante/handler_options.go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams     := options.EvmKeeper.GetParams(ctx)          // read once at construction
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // read once at construction
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

`NewDynamicFeeChecker` captures that pointer in a closure and uses it on every Cosmos tx validation call:

```go
// ante/evm/fee_checker.go
func NewDynamicFeeChecker(..., feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams) // stale!
        ...
        if feeCap.LT(baseFeeInt) { return nil, 0, ErrInsufficientFee }
        effectivePrice := types.EffectiveGasPrice(baseFeeInt.BigInt(), ...)
        effectiveFee   := effectivePrice.Mul(gas)
``` [2](#0-1) 

Meanwhile, `feemarket.BeginBlock` recalculates and writes the new base fee to the KV store every block:

```go
// x/feemarket/keeper/abci.go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)   // updates params.BaseFee in KV store
``` [3](#0-2) 

`SetBaseFee` writes the new value into the persistent KV store via `SetParams`:

```go
// x/feemarket/keeper/params.go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [4](#0-3) 

The struct pointed to by the closure is **never updated** when the KV store changes. The `feemarketParams.BaseFee` field in the closure always holds the value from ante handler construction time (genesis or last app restart), not the current block's base fee.

The Ethereum ante handler avoids this problem by calling `EVMBlockConfig` per-transaction, which reads fresh state from the object store (itself populated from the KV store at the start of each block):

```go
// evmd/ante/handler_options.go – Ethereum path
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
baseFee := blockCfg.BaseFee   // always current-block value
``` [5](#0-4) 

The Cosmos tx path has no equivalent per-call refresh.

### Impact Explanation

When the base fee rises above its initialization value (blocks consistently above the gas target), Cosmos native transactions can be accepted with a `feeCap` that is above the stale base fee but below the current base fee. The `DynamicFeeChecker` computes `effectiveFee` using the stale (lower) `baseFee`, so:

1. Transactions that should be rejected (`feeCap < current_baseFee`) pass the `feeCap.LT(baseFeeInt)` guard.
2. `effectiveFee` is computed as `min(stale_baseFee + tip, feeCap) * gas` — lower than the protocol-mandated amount.
3. The fee collector receives less than the required `current_baseFee * gas`.
4. The sender pays less than required; the shortfall is never collected.

This is a fee market bypass for Cosmos native transactions that permits under-priced transactions to commit and causes fees to be mis-accounted — matching the "High. fee market, ante handler … bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted" impact category.

### Likelihood Explanation

The base fee changes every block based on gas usage. Any unprivileged user submitting a Cosmos native transaction after the base fee has risen above its initialization value can exploit this. No special privileges, governance access, or validator collusion are required. The condition is met on any active chain where blocks are consistently above the gas target (a normal operating condition).

### Recommendation

`NewDynamicFeeChecker` should accept a `FeeMarketKeeper` interface and read fresh params on every invocation, rather than capturing a pointer to a stale struct:

```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params,
    fmKeeper FeeMarketKeeper) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        feemarketParams := fmKeeper.GetParams(ctx)   // fresh every call
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &feemarketParams)
        ...
    }
}
```

Alternatively, `newCosmosAnteHandler` should be rebuilt per-block (as the Ethereum ante handler effectively is via `EVMBlockConfig`), or the `DynamicFeeChecker` should call `fmKeeper.GetBaseFee(ctx)` directly.

### Proof of Concept

1. Chain starts; genesis base fee = 1,000,000,000 wei. `newCosmosAnteHandler` is called; `feemarketParams.BaseFee = 1,000,000,000` is captured in the closure.
2. Blocks are consistently above the gas target. After N blocks, `BeginBlock` has updated the KV-store base fee to 2,000,000,000 wei. The closure's `feemarketParams.BaseFee` remains 1,000,000,000.
3. Attacker submits a Cosmos native tx (e.g., `MsgSend`) with `feeCap = 1,500,000,000` and `gas = 100,000`.
4. `NewDynamicFeeChecker` computes `baseFee = 1,000,000,000` (stale). The check `feeCap (1.5B) < baseFee (1B)` is false → tx passes.
5. `effectiveFee = min(1,000,000,000 + tip, 1,500,000,000) * 100,000` — computed with stale base fee.
6. The tx is included; the fee collector receives ≈ 150,000,000,000 instead of the required ≥ 200,000,000,000.
7. The attacker saves ~25% on fees; the shortfall is never collected by the protocol. [6](#0-5) [1](#0-0)

### Citations

**File:** evmd/ante/handler_options.go (L86-96)
```go
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
	return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
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

**File:** evmd/ante/handler_options.go (L178-188)
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
```

**File:** ante/evm/fee_checker.go (L42-109)
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

		bigPriority := effectivePrice.Sub(baseFeeInt).Quo(types.DefaultPriorityReduction)
		priority := int64(math.MaxInt64)

		if bigPriority.IsInt64() {
			priority = bigPriority.Int64()
		}

		return effectiveFee, priority, nil
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

**File:** x/feemarket/keeper/params.go (L72-79)
```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
	params := k.GetParams(ctx)
	params.BaseFee = ethermint.SaturatedNewInt(baseFee)
	err := k.SetParams(ctx, params)
	if err != nil {
		return
	}
}
```
