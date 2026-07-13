### Title
Stale Fee Market Parameters in Cosmos Ante Handler Enable Fee Bypass After Governance `MsgUpdateParams` - (File: `evmd/ante/handler_options.go`)

### Summary

`newCosmosAnteHandler` snapshots `feemarketParams` once at construction time and passes a pointer to that local copy into both `MinGasPriceDecorator` and `NewDynamicFeeChecker`. When governance later executes `MsgUpdateParams` to raise `MinGasPrice` or update `BaseFee`, the store is updated but the ante handler's frozen snapshot is never refreshed. Every subsequent Cosmos transaction is validated against the stale threshold, not the live one.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads fee market parameters exactly once at construction time:

```go
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot taken once
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
...
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

The pointer `&feemarketParams` is stored inside `MinGasPriceDecorator.feemarketParams` and inside the closure returned by `NewDynamicFeeChecker`. Both consumers read from this frozen copy on every subsequent transaction:

```go
// MinGasPriceDecorator.AnteHandle
minGasPrice := mpd.feemarketParams.MinGasPrice   // always the construction-time value
``` [2](#0-1) 

```go
// NewDynamicFeeChecker closure
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)  // stale feemarketParams
``` [3](#0-2) 

`MinGasPriceDecorator` even holds a live `feesKeeper` that could supply fresh params, but it is never consulted: [4](#0-3) 

By contrast, the **EVM** ante handler (`newEthAnteHandler`) correctly re-reads params on every invocation via `EVMBlockConfig`:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
feemarketParams := &blockCfg.FeeMarketParams   // fresh every call
``` [5](#0-4) 

The asymmetry means only Cosmos-path transactions are affected.

Governance updates `MinGasPrice` and `BaseFee` through `MsgUpdateParams`, which calls `SetParams` and writes the new values to the KV store: [6](#0-5) [7](#0-6) 

The base fee is also updated every block by `BeginBlock → CalculateBaseFee → SetBaseFee`, which overwrites `params.BaseFee` in the store: [8](#0-7) [9](#0-8) 

Neither update is visible to the frozen `feemarketParams` pointer held by the Cosmos ante handler.

### Impact Explanation

After governance raises `MinGasPrice` (e.g., from 0 to 100 aevmos), Cosmos SDK transactions whose `gasPrice` falls between the old and new threshold pass `MinGasPriceDecorator` and are included in blocks. Fees that should be rejected are accepted and deducted at the under-priced rate, mis-accounting user fees and undermining the fee market invariant. Because `BaseFee` is also stale, `NewDynamicFeeChecker` computes an incorrect effective fee for every Cosmos transaction from the first block onward, allowing transactions priced below the live EIP-1559 base fee to commit. This satisfies the allowed High impact: **fee market ante handler bug that permits invalid transactions to commit and causes valid user funds/fees to be mis-accounted**.

### Likelihood Explanation

The trigger is a routine governance `MsgUpdateParams` call — a normal, expected chain operation. No special attacker capability is required beyond submitting a Cosmos transaction after any governance fee parameter update. The bug is persistent: it affects every Cosmos transaction for the entire lifetime of the node process after the update.

### Recommendation

1. In `MinGasPriceDecorator.AnteHandle`, replace `mpd.feemarketParams.MinGasPrice` with a live read: `mpd.feesKeeper.GetParams(ctx).MinGasPrice`. The keeper is already stored in the struct for exactly this purpose.
2. In `NewDynamicFeeChecker`, either accept a `FeeMarketKeeper` and call `GetParams(ctx)` inside the closure, or follow the pattern of `newEthAnteHandler` and read `feemarketParams` from `EVMBlockConfig` on each invocation.
3. Apply the same fix to `newLegacyCosmosAnteHandlerEip712` in `evmd/ante/evm_handler.go`, which has the identical construction-time snapshot pattern. [10](#0-9) 

### Proof of Concept

1. Chain starts with `MinGasPrice = 0` and `BaseFee = 1_000_000_000` (1 Gwei). `newCosmosAnteHandler` is called; `feemarketParams` snapshot captures these values.
2. Governance submits and passes `MsgUpdateParams` setting `MinGasPrice = 10_000_000_000` (10 Gwei). The KV store is updated; the ante handler snapshot is not.
3. Attacker submits a Cosmos `MsgSend` with `gasPrice = 1` (below the new 10 Gwei minimum).
4. `MinGasPriceDecorator.AnteHandle` reads `mpd.feemarketParams.MinGasPrice` → still `0` → short-circuits and passes the transaction.
5. `NewDynamicFeeChecker` computes `baseFee` from the stale `feemarketParams.BaseFee` (initial value) rather than the current block's base fee → fee check passes.
6. The under-priced Cosmos transaction is included in the block and committed, bypassing the governance-mandated fee floor.

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

**File:** ante/cosmos/min_gas_price.go (L36-46)
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
```

**File:** ante/cosmos/min_gas_price.go (L54-58)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
```

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

**File:** x/feemarket/keeper/msg_server.go (L16-27)
```go
func (k *Keeper) UpdateParams(goCtx context.Context, req *types.MsgUpdateParams) (*types.MsgUpdateParamsResponse, error) {
	if k.authority.String() != req.Authority {
		return nil, errorsmod.Wrapf(govtypes.ErrInvalidSigner, "invalid authority; expected %s, got %s", k.authority.String(), req.Authority)
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := k.SetParams(ctx, req.Params); err != nil {
		return nil, err
	}

	return &types.MsgUpdateParamsResponse{}, nil
}
```

**File:** x/feemarket/keeper/params.go (L40-48)
```go
func (k Keeper) SetParams(ctx sdk.Context, p types.Params) error {
	if err := p.Validate(); err != nil {
		return err
	}
	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&p)
	store.Set(types.ParamsKey, bz)

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

**File:** evmd/ante/evm_handler.go (L28-62)
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
		authante.NewValidateMemoDecorator(options.AccountKeeper),
		authante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		authante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
		authante.NewSetPubKeyDecorator(options.AccountKeeper),
		authante.NewValidateSigCountDecorator(options.AccountKeeper),
		authante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		// Note: signature verification uses EIP instead of the cosmos signature validator
		cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		authante.NewIncrementSequenceDecorator(options.AccountKeeper),
		ibcante.NewRedundantRelayDecorator(options.IBCKeeper),
	)
	decorators = append(decorators, extra...)
	return sdk.ChainAnteDecorators(decorators...)
}
```
