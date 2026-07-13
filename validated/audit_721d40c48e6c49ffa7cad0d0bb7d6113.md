### Title
Stale `feemarketParams` Snapshot in `newCosmosAnteHandler` Bypasses Governance-Raised `MinGasPrice` — (`File: evmd/ante/handler_options.go`)

---

### Summary

`newCosmosAnteHandler` captures `feemarketParams` as a local value-copy at construction time and passes a pointer to that copy into `MinGasPriceDecorator` and `NewDynamicFeeChecker`. After governance raises `MinGasPrice`, both decorators continue to enforce the old (lower) floor for the lifetime of the running node, allowing any user to submit Cosmos SDK transactions with fees below the new governance-mandated minimum.

---

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads `feemarketParams` once at construction:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams        := options.EvmKeeper.GetParams(ctx)          // line 179 – snapshot
    feemarketParams  := options.FeeMarketKeeper.GetParams(ctx)    // line 180 – snapshot
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)  // line 187
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams)  // line 198
```

`feemarketParams` is a local `feemarkettypes.Params` value. Go's escape analysis moves it to the heap when its address is taken, but the heap object is a **frozen copy** of the params at the moment `newCosmosAnteHandler` was called. It is never refreshed from the keeper's store.

`MinGasPriceDecorator.AnteHandle` reads directly from this frozen pointer:

```go
minGasPrice := mpd.feemarketParams.MinGasPrice   // line 54 – stale
```

`NewDynamicFeeChecker` similarly uses the frozen pointer to derive `baseFee`:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)  // line 56 – stale
```

The contrast with `newEthAnteHandler` (same file, line 86) makes the design intent clear: the EVM ante handler correctly fetches live params **per-invocation** inside its closure:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())  // line 88
feemarketParams := &blockCfg.FeeMarketParams   // line 94 – fresh every call
```

`newCosmosAnteHandler` never performs an equivalent live read; it relies entirely on the construction-time snapshot. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

After a governance proposal successfully raises `MinGasPrice` (e.g., from 0 to 10 aevmos), the on-chain keeper stores the new value, but every Cosmos SDK transaction (bank sends, staking, governance votes, IBC relays, authz execs, etc.) continues to be validated against the **old** `MinGasPrice` captured at node startup. Any user can submit transactions with fees below the new floor and have them accepted and committed to state. This directly mis-accounts fees: the chain's fee policy is violated, validators receive less than the governance-mandated minimum, and the economic invariant that "tightening is immediately effective" is broken.

This matches the allowed impact: **"High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."** [5](#0-4) 

---

### Likelihood Explanation

- `MinGasPrice` is a standard governance-adjustable parameter. Any chain using Ethermint that raises `MinGasPrice` via governance is immediately exposed.
- No special privileges are required: any unprivileged user who observes the governance proposal can begin submitting under-priced Cosmos SDK transactions the moment the proposal passes, and they will continue to be accepted until the node is restarted.
- The bug is persistent across all blocks after the governance change; it does not self-heal.
- The EVM ante handler is unaffected (it fetches live params), so the discrepancy may go unnoticed during normal monitoring. [6](#0-5) [7](#0-6) 

---

### Recommendation

Mirror the pattern used in `newEthAnteHandler`: move the `GetParams` calls **inside** the returned closure so they execute per-invocation against the live store, or pass the keeper itself and call `GetParams(ctx)` inside `AnteHandle`.

For `MinGasPriceDecorator`, replace the cached field read:

```go
// Before (stale):
minGasPrice := mpd.feemarketParams.MinGasPrice

// After (live):
minGasPrice := mpd.feesKeeper.GetParams(ctx).MinGasPrice
```

The `feesKeeper` field is already present in `MinGasPriceDecorator` but is never used for param reads. The `feemarketParams *feemarkettypes.Params` field and constructor parameter should be removed.

For `NewDynamicFeeChecker`, replace the captured `feemarketParams` pointer with a live keeper read inside the closure, consistent with how `newEthAnteHandler` obtains `blockCfg.FeeMarketParams` per call. [8](#0-7) [9](#0-8) 

---

### Proof of Concept

1. Node starts with `MinGasPrice = 0`. `newCosmosAnteHandler` is called once; `feemarketParams.MinGasPrice = 0` is frozen in the heap copy.
2. Governance proposal passes, setting `MinGasPrice = 10`. `FeeMarketKeeper.SetParams` writes the new value to the KV store.
3. Attacker submits a `MsgSend` Cosmos SDK transaction with `gasPrice = 0` (fee = 0).
4. `MinGasPriceDecorator.AnteHandle` reads `mpd.feemarketParams.MinGasPrice` → still `0` (stale). The short-circuit at line 57 fires (`minGasPrice.IsZero()`) and the check is skipped entirely.
5. The transaction passes ante and is committed to state, paying zero fees despite the governance-mandated floor of 10.
6. The same applies to `NewDynamicFeeChecker`: `types.GetBaseFee` is called with the stale `feemarketParams`, which may return `nil` (if `NoBaseFee` was flipped by governance) or the wrong base fee, causing the fee checker to fall back to validator-local min-gas-prices and bypass the global floor. [10](#0-9) [11](#0-10) [12](#0-11)

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
