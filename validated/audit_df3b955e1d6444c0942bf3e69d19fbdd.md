### Title
Stale Fee Market Params Snapshot in `newCosmosAnteHandler` Bypasses EIP-1559 Base Fee and `MinGasPrice` Enforcement - (`evmd/ante/handler_options.go`)

### Summary

`newCosmosAnteHandler` reads `feemarketParams` from the store exactly once at construction time and passes a pointer to that local snapshot into both `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Because the base fee is updated every block and `MinGasPrice` can be changed by governance, every Cosmos transaction processed after the first block uses a stale fee-market state for its fee validation, allowing under-priced transactions to commit.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` snapshots `feemarketParams` at construction time:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams    := options.EvmKeeper.GetParams(ctx)          // snapshot
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx) // snapshot
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

Both consumers store and dereference this pointer on every transaction:

- `NewDynamicFeeChecker` calls `types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)`, which reads `feemarketParams.GetBaseFee()` — the stale `BaseFee` field from the snapshot. [2](#0-1) [3](#0-2) 

- `MinGasPriceDecorator.AnteHandle` reads `mpd.feemarketParams.MinGasPrice` directly from the captured pointer. [4](#0-3) 

The base fee is updated **every block** in `BeginBlock` via `k.SetBaseFee(ctx, baseFee)`, which writes the new value into the KV store. [5](#0-4) 

`SetBaseFee` updates `params.BaseFee` in the persistent store, but the in-memory snapshot held by the ante handler is never refreshed. [6](#0-5) 

The contrast with `newEthAnteHandler` is explicit: it calls `options.EvmKeeper.EVMBlockConfig(ctx, ...)` on **every invocation**, obtaining a fresh `FeeMarketParams` and `BaseFee` for each transaction. [7](#0-6) 

The same stale-snapshot pattern is also present in `newLegacyCosmosAnteHandlerEip712`. [8](#0-7) 

### Impact Explanation

**Fee market bypass for Cosmos transactions (including EIP-712 wrapped EVM txs):**

1. **Stale base fee in `NewDynamicFeeChecker`**: The base fee changes every block. After the first block, `feemarketParams.BaseFee` in the snapshot diverges from the live value. If the live base fee has risen (e.g., due to sustained high gas usage), the fee checker accepts Cosmos transactions whose `feeCap` is below the current base fee. The `effectiveFee` deducted is computed from the stale (lower) base fee, so users pay less than the protocol requires — a direct fee mis-accounting.

2. **Stale `MinGasPrice` in `MinGasPriceDecorator`**: After a governance `MsgUpdateParams` raises `MinGasPrice`, the decorator continues enforcing the old lower threshold. Cosmos transactions with fees below the new minimum are accepted and committed.

Both paths satisfy: *"fee market, ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The base fee changes on every block by design, so the stale-base-fee path is triggered for every Cosmos transaction submitted after block 1. The `MinGasPrice` staleness requires a governance parameter update, which is a routine chain operation. No special attacker capability is needed beyond submitting a standard Cosmos transaction.

### Recommendation

Refactor `newCosmosAnteHandler` to follow the same pattern as `newEthAnteHandler`: read `feemarketParams` (and `evmParams`) fresh from the store on every invocation inside the returned closure, rather than capturing a one-time snapshot at construction time.

```go
func newCosmosAnteHandler(options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        evmParams       := options.EvmKeeper.GetParams(ctx)
        feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
        // build and run decorators using fresh params ...
    }
}
```

Apply the same fix to `newLegacyCosmosAnteHandlerEip712`.

### Proof of Concept

1. Chain starts; `newCosmosAnteHandler` is called with genesis context. `feemarketParams.BaseFee = 1_000_000_000` (default).
2. Network activity drives the base fee up over N blocks to `5_000_000_000` (stored in KV store, updated each `BeginBlock`).
3. Attacker submits a Cosmos `MsgSend` (or EIP-712 wrapped EVM tx) with `feeCap / gas = 2_000_000_000`.
4. `NewDynamicFeeChecker` evaluates `feeCap (2e9) >= stale_baseFee (1e9)` → **passes**.
5. `effectiveFee = min(1e9 + tip, 2e9) * gas` — computed from the stale base fee, not the live `5e9`.
6. The transaction commits; the user pays ~2× less than the current protocol-required fee.
7. Simultaneously, if governance raised `MinGasPrice` from `0` to `3e9`, the `MinGasPriceDecorator` still checks against `0` and passes the transaction unconditionally. [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11)

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

**File:** evmd/ante/handler_options.go (L178-212)
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
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
		ante.NewSetPubKeyDecorator(options.AccountKeeper),
		ante.NewValidateSigCountDecorator(options.AccountKeeper),
		ante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		ante.NewSigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		ante.NewIncrementSequenceDecorator(options.AccountKeeper),
		ibcante.NewRedundantRelayDecorator(options.IBCKeeper),
	)
	decorators = append(decorators, extra...)
	return sdk.ChainAnteDecorators(decorators...)
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

**File:** ante/cosmos/min_gas_price.go (L36-91)
```go
type MinGasPriceDecorator struct {
	feesKeeper      interfaces.FeeMarketKeeper
	evmDenom        string
	feemarketParams *feemarkettypes.Params
}

// NewMinGasPriceDecorator creates a new MinGasPriceDecorator instance used only for
// Cosmos transactions.
func NewMinGasPriceDecorator(fk interfaces.FeeMarketKeeper, evmDenom string, feemarketParams *feemarkettypes.Params) MinGasPriceDecorator {
	return MinGasPriceDecorator{feesKeeper: fk, evmDenom: evmDenom, feemarketParams: feemarketParams}
}

func (mpd MinGasPriceDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (newCtx sdk.Context, err error) {
	feeTx, ok := tx.(sdk.FeeTx)
	if !ok {
		return ctx, errorsmod.Wrapf(errortypes.ErrInvalidType, "invalid transaction type %T, expected sdk.FeeTx", tx)
	}

	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
	minGasPrices := sdk.DecCoins{
		{
			Denom:  mpd.evmDenom,
			Amount: minGasPrice,
		},
	}

	feeCoins := feeTx.GetFee()
	gas := feeTx.GetGas()

	requiredFees := make(sdk.Coins, 0)

	// Determine the required fees by multiplying each required minimum gas
	// price by the gas limit, where fee = ceil(minGasPrice * gasLimit).
	gasLimit := sdkmath.LegacyNewDecFromBigInt(new(big.Int).SetUint64(gas))

	for _, gp := range minGasPrices {
		fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()
		if fee.IsPositive() {
			requiredFees = requiredFees.Add(sdk.Coin{Denom: gp.Denom, Amount: fee})
		}
	}

	if !feeCoins.IsAnyGTE(requiredFees) {
		return ctx, errorsmod.Wrapf(errortypes.ErrInsufficientFee,
			"provided fee < minimum global fee (%s < %s). Please increase the gas price.",
			feeCoins,
			requiredFees)
	}

	return next(ctx, tx, simulate)
}
```

**File:** x/feemarket/keeper/abci.go (L29-51)
```go
// BeginBlock updates base fee
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

**File:** evmd/ante/evm_handler.go (L28-47)
```go
func newLegacyCosmosAnteHandlerEip712(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker authante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
	decorators := make([]sdk.AnteDecorator, 0, 15+len(extra))
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
		authante.NewSetUpContextDecorator(),
		authante.NewValidateBasicDecorator(),
		authante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
```
