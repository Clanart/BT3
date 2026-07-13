### Title
Stale `feemarketParams` Snapshot in Cosmos Ante Handler Enables Persistent Fee Market Bypass - (File: evmd/ante/handler_options.go)

### Summary
`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` snapshot `feemarketParams` once at construction time (app startup) and pass a pointer to this frozen snapshot into `NewDynamicFeeChecker` and `NewMinGasPriceDecorator`. Because the EIP-1559 base fee is updated every block via `BeginBlock`, the Cosmos-SDK ante path permanently enforces the fee floor that was current at node startup, not the live one. Any unprivileged user can submit Cosmos SDK transactions whose fee satisfies only the stale (lower) base fee, bypassing the current fee market requirement and systematically under-paying fees for the entire lifetime of the node.

### Finding Description

In `evmd/ante/handler_options.go`, both `newCosmosAnteHandler` (line 179) and `newLegacyCosmosAnteHandlerEip712` (line 29) fetch `feemarketParams` once from the keeper at construction time and capture a pointer to the local copy in the returned closures:

```go
// evmd/ante/handler_options.go:179-187
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // ← snapshot at startup
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams)
``` [1](#0-0) 

The pointer `&feemarketParams` is captured in the closure returned by `NewDynamicFeeChecker`:

```go
// ante/evm/fee_checker.go:42,56
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)  // ← stale pointer
``` [2](#0-1) 

And in `NewMinGasPriceDecorator`:

```go
// ante/cosmos/min_gas_price.go:44,54
func NewMinGasPriceDecorator(fk interfaces.FeeMarketKeeper, evmDenom string, feemarketParams *feemarkettypes.Params) MinGasPriceDecorator {
    return MinGasPriceDecorator{feesKeeper: fk, evmDenom: evmDenom, feemarketParams: feemarketParams}
}
...
minGasPrice := mpd.feemarketParams.MinGasPrice  // ← stale pointer
``` [3](#0-2) 

Meanwhile, the EIP-1559 base fee is updated every block in `BeginBlock` by writing a new value to the KVStore:

```go
// x/feemarket/keeper/abci.go:30-38
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)  // ← writes to KVStore, NOT to the in-memory snapshot
``` [4](#0-3) 

`SetBaseFee` allocates a new `Params` struct and writes it to the KVStore — it never touches the in-memory struct pointed to by the captured `&feemarketParams`:

```go
// x/feemarket/keeper/params.go:72-78
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)  // ← new struct written to KVStore
``` [5](#0-4) 

The critical asymmetry: the **Ethereum** ante handler (`newEthAnteHandler`) fetches fresh params on every single transaction call via `EVMBlockConfig`, while the **Cosmos** ante handler uses the frozen startup snapshot for the entire node lifetime:

```go
// evmd/ante/handler_options.go:86-95
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        feemarketParams := &blockCfg.FeeMarketParams  // ← fresh every call
        baseFee := blockCfg.BaseFee                   // ← fresh every call
``` [6](#0-5) 

`setAnteHandler` in `evmd/app.go` calls `ante.NewAnteHandler` exactly once at app startup, so the snapshot is never refreshed: [7](#0-6) 

### Impact Explanation

Any user submitting a Cosmos

### Citations

**File:** evmd/ante/handler_options.go (L86-95)
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

**File:** ante/evm/fee_checker.go (L42-56)
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
```

**File:** ante/cosmos/min_gas_price.go (L44-54)
```go
func NewMinGasPriceDecorator(fk interfaces.FeeMarketKeeper, evmDenom string, feemarketParams *feemarkettypes.Params) MinGasPriceDecorator {
	return MinGasPriceDecorator{feesKeeper: fk, evmDenom: evmDenom, feemarketParams: feemarketParams}
}

func (mpd MinGasPriceDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (newCtx sdk.Context, err error) {
	feeTx, ok := tx.(sdk.FeeTx)
	if !ok {
		return ctx, errorsmod.Wrapf(errortypes.ErrInvalidType, "invalid transaction type %T, expected sdk.FeeTx", tx)
	}

	minGasPrice := mpd.feemarketParams.MinGasPrice
```

**File:** x/feemarket/keeper/abci.go (L30-38)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

	// return immediately if base fee is nil
	if baseFee == nil {
		return nil
	}

	k.SetBaseFee(ctx, baseFee)
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

**File:** evmd/app.go (L783-808)
```go
func (app *EthermintApp) setAnteHandler(txConfig client.TxConfig, mempoolMaxTxs int) {
	anteHandler, err := ante.NewAnteHandler(ante.HandlerOptions{
		AccountKeeper:          app.AccountKeeper,
		BankKeeper:             app.BankKeeper,
		SignModeHandler:        txConfig.SignModeHandler(),
		FeegrantKeeper:         app.FeeGrantKeeper,
		SigGasConsumer:         ante.DefaultSigVerificationGasConsumer,
		IBCKeeper:              app.IBCKeeper,
		EvmKeeper:              app.EvmKeeper,
		FeeMarketKeeper:        app.FeeMarketKeeper,
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
		DisabledAuthzMsgs: []string{
			sdk.MsgTypeURL(&evmtypes.MsgEthereumTx{}),
			sdk.MsgTypeURL(&vestingtypes.MsgCreateVestingAccount{}),
			sdk.MsgTypeURL(&vestingtypes.MsgCreatePermanentLockedAccount{}),
			sdk.MsgTypeURL(&vestingtypes.MsgCreatePeriodicVestingAccount{}),
		},
		PendingTxListener: app.onPendingTx,
		AnteCache:         cache.NewAnteCache(mempoolMaxTxs),
	})
	if err != nil {
		panic(err)
	}

	app.SetAnteHandler(anteHandler)
```
