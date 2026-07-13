### Title
Stale `feemarketParams` Captured at Ante Handler Construction Time Allows Cosmos SDK Transactions to Bypass EIP-1559 Fee Market - (File: `ante/evm/fee_checker.go`, `evmd/ante/handler_options.go`)

### Summary

`NewDynamicFeeChecker` in `ante/evm/fee_checker.go` accepts a `*feemarkettypes.Params` pointer that is captured once at ante handler construction time in `newCosmosAnteHandler`. Because the `BaseFee` field inside that struct is never refreshed after construction, the fee checker permanently uses the genesis-era base fee for all Cosmos SDK transaction fee validation, regardless of how many blocks have elapsed and how much the EIP-1559 base fee has changed. An unprivileged user can submit Cosmos SDK transactions priced at the genesis base fee and have them admitted even when the live base fee is orders of magnitude higher.

### Finding Description

**Root cause — stale params pointer captured at construction:**

`newCosmosAnteHandler` reads `feemarketParams` once from the KV-store at the moment the ante handler is built, then passes a pointer to that local copy into `NewDynamicFeeChecker`:

```go
// evmd/ante/handler_options.go  (lines 178-211)
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams      := options.EvmKeeper.GetParams(ctx)       // snapshot at construction
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx) // snapshot at construction
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

The returned `TxFeeChecker` closure then reads `BaseFee` from that captured pointer on every subsequent transaction:

```go
// ante/evm/fee_checker.go  (lines 42-60)
func NewDynamicFeeChecker(ethCfg *params.ChainConfig,
    evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
        // feemarketParams is the pointer captured at construction — never refreshed
``` [2](#0-1) 

`types.GetBaseFee` simply reads `feemarketParams.BaseFee`:

```go
// x/evm/types/utils.go  (lines 244-254)
func GetBaseFee(height int64, ethCfg *params.ChainConfig,
    feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    return feemarketParams.GetBaseFee()   // reads the in-memory struct, not the KV-store
}
``` [3](#0-2) 

**Contrast with the EVM ante handler — which is correct:**

`newEthAnteHandler` does *not* take a `ctx` parameter and does *not* capture params at construction. Instead, it calls `EVMBlockConfig` on every transaction, which reads the freshly-updated base fee from the object store:

```go
// evmd/ante/handler_options.go  (lines 86-96)
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        baseFee := blockCfg.BaseFee   // always current
``` [4](#0-3) 

The asymmetry is intentional for EVM transactions but was not applied to the Cosmos SDK path.

**How the base fee is updated each block (but never reaches the Cosmos checker):**

`feemarket.BeginBlock` → `CalculateBaseFee` → `SetBaseFee` → `SetParams` → KV-store write. The in-memory struct pointed to by the captured `feemarketParams` is never touched. [5](#0-4) [6](#0-5) 

### Impact Explanation

Any user can craft a Cosmos SDK transaction (e.g., `MsgSend`, `MsgDelegate`, governance votes, IBC relayer messages) and set fees equal to `genesis_base_fee × gas_limit`. If the live base fee has risen above the genesis value — which is the normal outcome of sustained block utilisation — the `NewDynamicFeeChecker` accepts the transaction because it compares against the stale genesis base fee. The transaction is included in a block and its fees are deducted at the genesis rate, not the current rate.

Conversely, if the base fee has fallen below the genesis value, legitimate transactions priced at the current base fee are incorrectly rejected.

This is a **High** impact fee market / ante handler bug: it permits invalid (under-priced) Cosmos SDK transactions to commit and causes valid user funds/fees to be mis-accounted, matching the allowed impact category.

### Likelihood Explanation

The bug is always present once the chain's base fee diverges from its genesis value, which happens within the first few blocks of any active chain. No special privileges are required — any user submitting a Cosmos SDK transaction triggers the vulnerable code path. The `DynamicFeeChecker` is enabled by default (`options.DynamicFeeChecker` flag) in the reference `newCosmosAnteHandler` wiring.

### Recommendation

Replace the one-time param snapshot in `newCosmosAnteHandler` with a live read inside the returned closure, mirroring the pattern used by `newEthAnteHandler`:

```go
func newCosmosAnteHandler(options HandlerOptions, ...) sdk.AnteHandler {
    // Do NOT read params here
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // fresh per-tx
        evmParams       := options.EvmKeeper.GetParams(ctx)
        txFeeChecker    := evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
        ...
    }
}
```

Alternatively, use `EVMBlockConfig` (which is already cached per-block in the object store) to supply the base fee, as the EVM ante handler does.

### Proof of Concept

1. Chain starts with genesis `BaseFee = 100_000_000_000` (100 Gwei). `newCosmosAnteHandler` is called; `feemarketParams.BaseFee = 100_000_000_000` is captured.
2. After N blocks of full utilisation, `feemarket.BeginBlock` raises the live base fee to `500_000_000_000` (500 Gwei) via `SetBaseFee` → KV-store write.
3. Attacker submits a Cosmos `MsgSend` with `fee = 100_000_000_000 × gas_limit` (priced at genesis base fee, 5× below current).
4. `NewDynamicFeeChecker` evaluates `feeCap = fee / gas = 100_000_000_000`. It compares against `baseFeeInt = feemarketParams.BaseFee = 100_000_000_000` (stale). Check passes: `feeCap >= baseFeeInt`.
5. Transaction is included in the block. The attacker paid 5× less than the required fee. All honest Cosmos SDK users who correctly priced at 500 Gwei paid 5× more than necessary. [7](#0-6) [8](#0-7)

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

**File:** ante/evm/fee_checker.go (L83-91)
```go
		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}

		// calculate the effective gas price using the EIP-1559 logic.
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
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

**File:** x/feemarket/keeper/abci.go (L30-52)
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

**File:** x/feemarket/keeper/eip1559.go (L32-46)
```go
func (k Keeper) CalculateBaseFee(ctx sdk.Context) *big.Int {
	params := k.GetParams(ctx)

	// Ignore the calculation if not enabled
	if !params.IsBaseFeeEnabled(ctx.BlockHeight()) {
		return nil
	}
	consParams := ctx.ConsensusParams()

	// If the current block is the first EIP-1559 block, return the base fee
	// defined in the parameters (DefaultBaseFee if it hasn't been changed by
	// governance).
	if ctx.BlockHeight() == params.EnableHeight {
		return params.BaseFee.BigInt()
	}
```
