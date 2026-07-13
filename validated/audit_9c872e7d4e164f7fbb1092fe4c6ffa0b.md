### Title
`NewDynamicFeeChecker` Uses Stale `feemarketParams` Captured at App Startup, Not Current Block Base Fee - (`File: ante/evm/fee_checker.go`)

### Summary

`NewDynamicFeeChecker`, used for Cosmos SDK (non-EVM) transaction fee validation, reads the base fee from a `*feemarkettypes.Params` pointer captured once at application startup. Because `SetBaseFee` only updates the KV store and never mutates the in-memory struct, the base fee used for Cosmos SDK transaction fee enforcement is permanently stale. Validators that restart at different times will hold different stale base fees, making the ante handler non-deterministic across the validator set and allowing under-priced Cosmos SDK transactions to commit.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` is called once at application initialization:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // captured once at startup
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

`feemarketParams` is a local value-type variable. Its address is passed to `NewDynamicFeeChecker`, which closes over it:

```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads directly from the captured in-memory struct:

```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()   // reads from captured struct, not KV store
``` [3](#0-2) 

Meanwhile, `SetBaseFee` (called every `BeginBlock`) only writes to the KV store and never updates the in-memory struct:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)   // writes to KV store only
``` [4](#0-3) 

The base fee is updated every block in `BeginBlock`:

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [5](#0-4) 

By contrast, the Ethereum transaction ante handler (`newEthAnteHandler`) reads the base fee fresh from the keeper on every transaction:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
baseFee := blockCfg.BaseFee
``` [6](#0-5) 

Only Cosmos SDK transactions (governance, staking, IBC, etc.) routed through `newCosmosAnteHandler` are affected.

### Impact Explanation

**Fee mis-accounting (High):** After the base fee rises post-startup, Cosmos SDK transactions paying fees between the stale startup base fee and the current base fee pass the `feeCap.LT(baseFeeInt)` check and are committed with insufficient fees. Users underpay; the fee market enforcement is bypassed.

**Consensus non-determinism (Critical):** Validators restart at different times (upgrades, maintenance). Each restart re-captures a different `feemarketParams.BaseFee`. During `DeliverTx`, the same Cosmos SDK transaction may pass the fee check on validators that started when the base fee was lower, and fail on validators that started when it was higher. This produces divergent ante handler results across the validator set, causing a deterministic consensus failure and potential chain halt.

### Likelihood Explanation

Validators restart regularly for software upgrades and maintenance. The EIP-1559 base fee adjusts every block (up to ±12.5%). Over a chain's lifetime, the base fee can drift substantially from any given startup value. Any Cosmos SDK transaction submitted with a fee in the gap between two validators' stale base fees triggers the divergence. This is an unprivileged, normal-user-reachable path requiring no special access.

### Recommendation

`NewDynamicFeeChecker` must read the base fee from the live KV store on every invocation, not from a captured pointer. Pass the `FeeMarketKeeper` interface instead of a `*feemarkettypes.Params` pointer, and call `keeper.GetBaseFee(ctx)` inside the returned closure. This mirrors how `newEthAnteHandler` already handles it via `EVMBlockConfig`.

### Proof of Concept

1. Chain starts at block 1 with base fee = 1,000,000,000.
2. Over many blocks the base fee rises to 2,000,000,000 (stored in KV store via `SetBaseFee`).
3. Validator A restarts at block 5,000; its `feemarketParams.BaseFee` = 2,000,000,000.
4. Validator B has been running since genesis; its `feemarketParams.BaseFee` = 1,000,000,000.
5. User submits a Cosmos SDK governance `MsgVote` with `fee = 1,500,000,000 * gasLimit`.
6. Validator B's `NewDynamicFeeChecker`: `feeCap (1,500,000,000) >= baseFeeInt (1,000,000,000)` → **accepts**.
7. Validator A's `NewDynamicFeeChecker`: `feeCap (1,500,000,000) < baseFeeInt (2,000,000,000)` → **rejects** with `ErrInsufficientFee`.
8. Block proposed by Validator B includes the transaction; Validator A rejects the block → consensus failure.

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
