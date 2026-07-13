### Title
Stale Fee Market Params Snapshot in `newCosmosAnteHandler` Allows Cosmos Transactions to Bypass Current Base Fee — (`evmd/ante/handler_options.go`)

### Summary
`newCosmosAnteHandler` captures `feemarketParams` (including `BaseFee`) once at app initialization time and passes a pointer to that frozen snapshot into `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Because the EIP-1559 base fee is updated every block in `BeginBlock`, the Cosmos ante handler's fee checker permanently uses a stale base fee, allowing Cosmos SDK transactions to be accepted and committed with fees below the current live base fee.

### Finding Description

`newCosmosAnteHandler` is called **once** during app initialization in `setAnteHandler`:

```go
// evmd/app.go
func (app *EthermintApp) setAnteHandler(...) {
    anteHandler, err := ante.NewAnteHandler(ante.HandlerOptions{...})
    app.SetAnteHandler(anteHandler)
}
```

Inside `newCosmosAnteHandler`, params are fetched from the keeper using the initialization-time context and then captured by pointer into the returned closure:

```go
// evmd/ante/handler_options.go:178-188
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)          // snapshot at startup
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx) // snapshot at startup
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

The `NewDynamicFeeChecker` closure captures `feemarketParams *feemarkettypes.Params` and reads `feemarketParams.BaseFee` on every transaction via `types.GetBaseFee`:

```go
// ante/evm/fee_checker.go:56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` which reads the `BaseFee` field of the frozen struct:

```go
// x/evm/types/utils.go:244-254
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()
    ...
}
``` [3](#0-2) 

Meanwhile, the live base fee is updated every block in `BeginBlock` → `CalculateBaseFee` → `SetBaseFee`, which writes the new value into the KV store:

```go
// x/feemarket/keeper/abci.go:30-38
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [4](#0-3) 

`SetBaseFee` writes the updated value into the params KV store:

```go
// x/feemarket/keeper/params.go:72-78
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [5](#0-4) 

The frozen `feemarketParams` pointer in the Cosmos ante handler closure is **never updated** to reflect these per-block writes. By contrast, `newEthAnteHandler` correctly fetches fresh params per transaction via `EVMBlockConfig`:

```go
// evmd/ante/handler_options.go:87-96
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        feemarketParams := &blockCfg.FeeMarketParams  // live, per-tx
        baseFee := blockCfg.BaseFee                   // live, per-tx
``` [6](#0-5) 

The asymmetry is the root cause: EVM transactions get live params; Cosmos transactions get the startup snapshot.

### Impact Explanation

When the base fee has risen above its startup value (e.g., after sustained high block utilization), a Cosmos SDK transaction can be submitted with:

```
feeCap = startup_baseFee  (< current_baseFee)
```

The `NewDynamicFeeChecker` compares `feeCap` against the stale `startup_baseFee` and accepts the transaction. The effective fee deducted is `startup_baseFee * gas` instead of `current_baseFee * gas`. The transaction is committed to the block with fees below the current EIP-1559 floor.

This fits the High impact category: **ante handler bug that permits invalid transactions to commit and causes valid user funds/fees to be mis-accounted** — the fee collector receives less than the current base fee requires, and the fee market mechanism is bypassed for all Cosmos transactions.

### Likelihood Explanation

- `DynamicFeeChecker: true` is set in production (`evmd/app.go:794`). [7](#0-6) 
- The base fee changes every block based on gas utilization. After any period of above-target utilization, the live base fee exceeds the startup value.
- Any unprivileged user can submit a Cosmos SDK transaction (IBC relayer messages, governance votes, staking operations, etc.) with a fee set to the stale startup base fee.
- No special privileges, keys, or validator cooperation required.

### Recommendation

Replace the stale snapshot pattern in `newCosmosAnteHandler` with per-transaction live reads, mirroring `newEthAnteHandler`. Specifically:

1. Remove the upfront `GetParams` calls from `newCosmosAnteHandler`.
2. Pass the live `FeeMarketKeeper` and `EvmKeeper` into `NewDynamicFeeChecker` and `MinGasPriceDecorator` so they call `GetParams(ctx)` inside the per-transaction closure, reading the current block's committed base fee from the KV store on each invocation.

### Proof of Concept

1. Record `startup_baseFee` = value of `feemarketParams.BaseFee` at chain start (e.g., `1_000_000_000` wei).
2. Submit many EVM transactions filling blocks above the gas target for N blocks. The live base fee rises to, say, `2_000_000_000` wei.
3. Submit a Cosmos SDK transaction (e.g., `MsgDelegate`) with `fee = startup_baseFee * gasLimit`.
4. Observe: `NewDynamicFeeChecker` compares `feeCap = startup_baseFee` against the stale `feemarketParams.BaseFee = startup_baseFee`, passes the check, and deducts only `startup_baseFee * gas` — half the required fee — while the transaction is committed to the block.
5. Confirm: an EVM transaction with the same `gasFeeCap = startup_baseFee` is correctly rejected by `newEthAnteHandler` because it reads the live `blockCfg.BaseFee = 2_000_000_000`.

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

**File:** ante/evm/fee_checker.go (L42-60)
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

**File:** evmd/app.go (L793-795)
```go
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
		DisabledAuthzMsgs: []string{
```
