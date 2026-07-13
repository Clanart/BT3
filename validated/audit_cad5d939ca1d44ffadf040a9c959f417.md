### Title
Stale Cached `feemarketParams` in `NewDynamicFeeChecker` Allows Cosmos Txs to Bypass Current EIP-1559 Base Fee Validation — (`ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` captures `feemarketParams` at ante handler construction time (app initialization). Because `BaseFee` inside `feemarketParams` is updated per-block by `BeginBlock` but the closure always reads from the stale captured pointer, Cosmos transactions (including legacy EIP-712 txs) can pass fee validation using an outdated lower base fee. This allows any unprivileged user to underpay fees relative to the current EIP-1559 market rate, causing mis-accounting of user funds and fee revenue.

---

### Finding Description

**Root cause — stale pointer capture in `NewDynamicFeeChecker`:**

`NewDynamicFeeChecker` is constructed once at ante handler setup time (app initialization) in `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712`:

```go
// evmd/ante/handler_options.go
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot at init
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

The pointer `&feemarketParams` points to a local variable whose value is fixed at construction time. Inside the returned closure, every call reads the base fee from this frozen snapshot:

```go
// ante/evm/fee_checker.go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` — the value stored in the captured struct, not the live KV store:

```go
// x/evm/types/utils.go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()   // reads from frozen snapshot
    ...
    return baseFee
}
``` [3](#0-2) 

**Contrast with the EVM ante handler path**, which reads the live base fee from the keeper on every call:

```go
// x/evm/keeper/keeper.go
baseFee := k.feeMarketKeeper.GetBaseFee(ctx)   // live store read
``` [4](#0-3) 

**Per-block base fee updates are invisible to the checker.** `CalculateBaseFee` computes a new base fee each block and stores it via `SetBaseFee` → `SetParams`:

```go
// x/feemarket/keeper/params.go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    k.SetParams(ctx, params)
}
``` [5](#0-4) 

This writes to the KV store, but the `feemarketParams` struct captured in the closure is never updated. The closure's `feemarketParams.BaseFee` remains the genesis value forever.

**Fee validation uses the stale base fee.** The checker compares the tx's `feeCap` against the stale `baseFeeInt`:

```go
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
effectiveFee := sdk.Coins{{ Denom: denom, Amount: effectivePrice.Mul(...) }}
``` [6](#0-5) 

If the live base fee has risen above the stale snapshot, a tx with `feeCap` between the stale and live base fee passes the check and has a lower effective fee deducted than the current market requires.

The same stale capture exists in the legacy EIP-712 ante handler path: [7](#0-6) 

---

### Impact Explanation

This is a **High** impact finding matching: *"fee market, ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

- Cosmos txs (including EIP-712 signed txs) can be submitted with a `feeCap` below the current live base fee but above the stale genesis-time base fee.
- The `NewDeductFeeDecorator` deducts the effective fee computed from the stale base fee — less than the current market rate.
- Users systematically underpay fees; validators and the fee collector receive less revenue than the EIP-1559 mechanism mandates.
- Because the base fee can only increase by ≤12.5% per block (`BaseFeeChangeDenominator = 8`), after sustained high utilization the stale value diverges significantly from the live value, widening the exploitable window.

---

### Likelihood Explanation

- `DynamicFeeChecker` must be enabled (`options.DynamicFeeChecker = true`), which is the intended production configuration for EIP-1559 chains.
- Any unprivileged user can craft a Cosmos tx (e.g., `MsgDelegate`, `MsgSend`) with a `feeCap` between the stale and live base fee and broadcast it.
- No special privileges, validator collusion, or key compromise required.
- The divergence grows monotonically with block count under any non-zero utilization, making exploitation increasingly easy over time.

---

### Recommendation

Inside the `NewDynamicFeeChecker` closure, read the base fee from the live keeper rather than from the frozen `feemarketParams` pointer. Pass a `FeeMarketKeeper` interface and call `GetBaseFee(ctx)` at call time:

```go
// ante/evm/fee_checker.go
func NewDynamicFeeChecker(
    ethCfg *params.ChainConfig,
    evmParams *types.Params,
    fmKeeper FeeMarketKeeper,   // replace *feemarkettypes.Params
) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := fmKeeper.GetBaseFee(ctx)   // live read per call
        if baseFee == nil { ... }
        ...
    }
}
```

Apply the same fix to `MinGasPriceDecorator` for `MinGasPrice` (lower urgency, as governance params change rarely).

---

### Proof of Concept

1. Chain launches with genesis base fee = `1_000_000_000` wei. `NewDynamicFeeChecker` captures `feemarketParams.BaseFee = 1_000_000_000`.
2. Sustained high block utilization causes the live base fee to rise to `2_000_000_000` after many blocks. The KV store is updated each block; the closure's snapshot is not.
3. Attacker submits a `MsgSend` Cosmos tx with `fees = 1_500_000_000 * gas` (above stale base fee, below live base fee).
4. `NewDynamicFeeChecker` evaluates `feeCap = 1_500_000_000 >= baseFeeInt = 1_000_000_000` → **passes**.
5. `effectivePrice = min(1_000_000_000 + tip, 1_500_000_000)` — computed from stale base fee.
6. `NewDeductFeeDecorator` deducts the under-priced effective fee. The tx commits. The attacker paid ~25% less than the current market rate; the fee collector is short-changed by the difference. [8](#0-7) [3](#0-2) [9](#0-8)

### Citations

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

**File:** x/evm/keeper/keeper.go (L310-320)
```go
func (k Keeper) getBaseFee(ctx sdk.Context, london bool) *big.Int {
	if !london {
		return nil
	}
	baseFee := k.feeMarketKeeper.GetBaseFee(ctx)
	if baseFee == nil {
		// return 0 if feemarket not enabled.
		baseFee = big.NewInt(0)
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
