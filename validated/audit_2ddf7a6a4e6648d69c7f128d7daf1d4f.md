### Title
Stale `feemarketParams` Snapshot in `NewDynamicFeeChecker` Enables Fee Market Bypass for Cosmos SDK Transactions - (File: `ante/evm/fee_checker.go`, `evmd/ante/handler_options.go`)

### Summary
`NewDynamicFeeChecker` captures `feemarketParams` as a pointer to a local variable at ante-handler construction time. Because the base fee (`params.BaseFee`) is updated every block in `BeginBlock` but the captured snapshot is never refreshed, the fee checker permanently uses a stale base fee for all Cosmos SDK transaction fee validation. This is the direct analog of the Chainlink `latestRoundData()` staleness bug: a critical value is read once and cached without any mechanism to detect or reject stale data.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` (and `newLegacyCosmosAnteHandlerEip712`) fetch fee market params once from the store and pass a pointer to that local copy into `NewDynamicFeeChecker`:

```go
// evmd/ante/handler_options.go
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot at construction
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)  // pointer to local
``` [1](#0-0) 

`NewDynamicFeeChecker` closes over that pointer and uses it for every subsequent transaction:

```go
// ante/evm/fee_checker.go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

The `feemarketParams` local variable is allocated on the heap (Go escape analysis) but is **never written again** after `newCosmosAnteHandler` returns. Meanwhile, the fee market keeper updates `params.BaseFee` in the store on every block in `BeginBlock`:

```go
// x/feemarket/keeper/abci.go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)   // writes new BaseFee to KV store
``` [3](#0-2) 

`SetBaseFee` writes through to `SetParams`, updating the persistent KV store:

```go
// x/feemarket/keeper/params.go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [4](#0-3) 

The closure in `NewDynamicFeeChecker` never calls `GetParams` again; it reads only the stale pointer. The same stale pointer is also passed to `MinGasPriceDecorator`:

```go
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [5](#0-4) 

### Impact Explanation

The `NewDynamicFeeChecker` is the fee-enforcement gate for all Cosmos SDK (non-EVM) transactions that use the EIP-1559 dynamic fee path. Because `feemarketParams.BaseFee` is frozen at the value present when the ante handler was constructed (typically at genesis or last node restart), the effective minimum fee enforced for Cosmos SDK transactions diverges from the live on-chain base fee after the first block.

- **Under-priced admission**: When the live base fee rises above the stale snapshot (e.g., after sustained high-gas blocks), the checker accepts Cosmos SDK transactions whose `feeCap` is below the true current base fee. Those transactions commit with fees that are insufficient under the current fee market, mis-accounting user funds and breaking EIP-1559 invariants.
- **Over-rejection**: When the live base fee falls below the snapshot (e.g., after a period of low activity), valid transactions are incorrectly rejected.

The first case directly satisfies: *"fee market, ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

In standard Cosmos SDK app wiring, `newCosmosAnteHandler` is called once during `NewApp()` initialization. The resulting `sdk.AnteHandler` function is stored and reused for every block and every transaction for the lifetime of the process. The base fee diverges from the snapshot after the very first block that changes gas usage. Any unprivileged user submitting a Cosmos SDK transaction (e.g., `MsgSend`, IBC, governance) with a fee calibrated to the stale base fee triggers the mis-accounting path. No special privileges, governance access, or validator collusion are required.

### Recommendation

`NewDynamicFeeChecker` should accept a `FeeMarketKeeper` interface (or equivalent) and call `GetParams(ctx)` (or `GetBaseFee(ctx)`) inside the returned closure at runtime, using the live `sdk.Context` already available as a closure parameter. The stale-snapshot pattern should be removed entirely:

```go
// Correct pattern: read live params inside the closure
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmKeeper EVMKeeper, fmKeeper FeeMarketKeeper) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        evmParams  := evmKeeper.GetParams(ctx)
        fmParams   := fmKeeper.GetParams(ctx)
        baseFee    := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &fmParams)
        ...
    }
}
```

The same fix applies to `MinGasPriceDecorator` if it also reads from the captured snapshot rather than the live keeper.

### Proof of Concept

1. Chain starts; genesis `BaseFee = 1_000_000_000` (1 Gwei). `newCosmosAnteHandler` is called once; `feemarketParams.BaseFee` is frozen at 1 Gwei.
2. Over N blocks of full blocks, `CalculateBaseFee` raises the live base fee to, say, 5 Gwei and writes it to the KV store via `SetBaseFee`.
3. Attacker submits a Cosmos SDK `MsgSend` with `feeCap = 1 Gwei` (the stale value).
4. `NewDynamicFeeChecker` evaluates `baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)` — `feemarketParams.BaseFee` is still 1 Gwei.
5. The check `feeCap.LT(baseFeeInt)` → `1 Gwei < 1 Gwei` is false; the transaction passes ante-handler validation and is committed despite paying 5× less than the current required base fee. [6](#0-5)

### Citations

**File:** evmd/ante/handler_options.go (L179-187)
```go
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker ante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
```

**File:** evmd/ante/handler_options.go (L198-198)
```go
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
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

**File:** ante/evm/fee_checker.go (L83-88)
```go
		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
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
