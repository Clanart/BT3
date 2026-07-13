### Title
Stale `feemarketParams` Snapshot in `newCosmosAnteHandler` Causes Cosmos SDK Transactions to Bypass Current Base Fee Enforcement - (File: `evmd/ante/handler_options.go`)

### Summary

`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` capture `feemarketParams` (including `BaseFee`) from the store **once at construction time** and pass a pointer to that snapshot into `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Because the base fee is updated every block in `BeginBlock`, all subsequent Cosmos SDK transactions processed by these ante handlers use a permanently stale base fee for fee validation and deduction, allowing fee underpayment or causing spurious rejections.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads `feemarketParams` once at construction:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // snapshot at construction
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)  // pointer to local
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
    ...
}
``` [1](#0-0) 

The same pattern appears in `newLegacyCosmosAnteHandlerEip712`: [2](#0-1) 

The returned `sdk.AnteHandler` closure captures `&feemarketParams` — a pointer to a struct populated once and never refreshed. Every time the ante handler runs for a Cosmos SDK transaction, `NewDynamicFeeChecker` calls:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

which reads `feemarketParams.GetBaseFee()` — the stale snapshot value — instead of the live store value. [3](#0-2) 

`types.GetBaseFee` reads directly from the passed `feemarketParams` pointer, never querying the keeper: [4](#0-3) 

By contrast, `newEthAnteHandler` (the EVM transaction path) correctly reads fresh params on **every invocation** via `EVMBlockConfig`: [5](#0-4) 

`EVMBlockConfig` reads `feemarketParams` fresh from the store each call: [6](#0-5) 

The base fee is updated every block in `BeginBlock` via `k.SetBaseFee(ctx, baseFee)` → `k.SetParams(ctx, params)`, writing the new value to the KV store: [7](#0-6) 

But the captured `feemarketParams` struct in `newCosmosAnteHandler` is never updated after construction, so it permanently diverges from the live store value.

### Impact Explanation

The `NewDynamicFeeChecker` result is consumed by `authante.NewDeductFeeDecorator`, which uses it to determine the `effectiveFee` to deduct from the sender and to enforce the `feeCap >= baseFee` check: [8](#0-7) 

**Scenario A — base fee rises (high block utilization):** The stale (lower) base fee is used. A Cosmos SDK transaction with `feeCap` between the stale base fee and the current base fee passes the `feeCap.LT(baseFeeInt)` check and is accepted. The `effectiveFee` deducted is computed from the stale base fee, so the sender pays less than the protocol currently requires. This is a direct fee mis-accounting: invalid transactions commit and user funds are under-charged.

**Scenario B — base fee falls:** Transactions with fees above the current (lower) base fee but below the stale (higher) base fee are incorrectly rejected, denying service to valid users.

The divergence compounds over time: with default `BaseFeeChangeDenominator=8` and `ElasticityMultiplier=2`, the base fee can shift by up to 12.5% per block. Over hundreds of blocks of sustained high utilization, the stale base fee can be a fraction of the live value.

### Likelihood Explanation

This affects every Cosmos SDK transaction (non-EVM) that uses the `DynamicFeeChecker` path (i.e., when `options.DynamicFeeChecker = true`). Any unprivileged user can submit a Cosmos SDK transaction with a fee calibrated to the stale base fee. The condition is triggered automatically whenever the base fee changes from its value at ante handler construction time, which happens every block under normal operation. No special privileges or coordination are required.

### Recommendation

Read `feemarketParams` fresh from the keeper inside the returned closure, not at construction time. Mirror the pattern used in `newEthAnteHandler`:

```go
func newCosmosAnteHandler(options HandlerOptions, ...) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // fresh per invocation
        evmParams := options.EvmKeeper.GetParams(ctx)
        ...
        // use feemarketParams inline, do not capture pointer across calls
    }
}
```

Alternatively, pass the `FeeMarketKeeper` into `NewDynamicFeeChecker` so it can call `GetParams(ctx)` at invocation time, consistent with how `newEthAnteHandler` uses `EVMBlockConfig`.

### Proof of Concept

1. Deploy a chain with `DynamicFeeChecker = true` for Cosmos SDK transactions.
2. Record the initial base fee `B0` at ante handler construction (e.g., `B0 = 1_000_000_000`).
3. Submit blocks with gas usage above the gas target for N blocks, causing the live base fee to rise to `B_live = 2_000_000_000`.
4. Submit a Cosmos SDK transaction (e.g., `MsgSend`) with `fee = B0 * gasLimit` (half the current required fee).
5. Observe: `NewDynamicFeeChecker` evaluates `feeCap >= baseFeeInt` using `baseFeeInt = B0`, so the check passes. The transaction is accepted and included in a block. The sender pays `B0 * gasLimit` instead of the required `B_live * gasLimit`, underpaying by 50%.
6. Confirm: submitting the same transaction through the EVM path (`newEthAnteHandler`) with the same fee would be correctly rejected because `EVMBlockConfig` reads the live `B_live`.

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

**File:** evmd/ante/handler_options.go (L178-201)
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
```

**File:** evmd/ante/evm_handler.go (L28-38)
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

**File:** ante/evm/fee_checker.go (L83-99)
```go
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

**File:** x/evm/keeper/config.go (L74-100)
```go
// EVMBlockConfig creates the EVMBlockConfig based on current state
func (k *Keeper) EVMBlockConfig(ctx sdk.Context, chainID *big.Int) (*EVMBlockConfig, error) {
	objStore := ctx.ObjectStore(k.objectKey)
	v := objStore.Get(types.KeyPrefixObjectParams)
	if v != nil {
		return v.(*EVMBlockConfig), nil
	}

	params := k.GetParams(ctx)
	ethCfg := params.ChainConfig.EthereumConfig(chainID)

	feemarketParams := k.feeMarketKeeper.GetParams(ctx)

	// get the coinbase address from the block proposer
	coinbase, err := k.GetCoinbaseAddress(ctx)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to obtain coinbase address")
	}

	var baseFee *big.Int
	if types.IsLondon(ethCfg, ctx.BlockHeight()) {
		baseFee = feemarketParams.GetBaseFee()
		// should not be nil if london hardfork enabled
		if baseFee == nil {
			baseFee = new(big.Int)
		}
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
