### Title
Stale `feemarketParams` Captured at Construction in `newCosmosAnteHandler` Bypasses `MinGasPrice` and Dynamic Fee Enforcement After Governance Update — (File: `evmd/ante/handler_options.go`)

---

### Summary

`newCosmosAnteHandler` reads `feemarketParams` once at construction time and passes a pointer to that captured local variable into `MinGasPriceDecorator` and `DynamicFeeChecker`. Neither decorator ever re-reads from the keeper. After a governance `MsgUpdateParams` raises `MinGasPrice`, the ante handler silently continues enforcing the old (lower) value, allowing Cosmos transactions with fees below the new governance-set minimum to be committed.

---

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads `feemarketParams` from the keeper exactly once at the moment the ante handler chain is constructed:

```go
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // line 180 — captured once
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)  // line 187
...
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),  // line 198
```

`feemarketParams` is a **value-type local variable**. `&feemarketParams` is a pointer to that stack-allocated copy. Both `MinGasPriceDecorator` and `DynamicFeeChecker` store this pointer and dereference it on every transaction:

```go
// ante/cosmos/min_gas_price.go line 54
minGasPrice := mpd.feemarketParams.MinGasPrice
```

```go
// ante/evm/fee_checker.go line 56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

Neither path calls back into the keeper to fetch the live value. The `MinGasPriceDecorator` even holds a live `feesKeeper` reference (line 37) but never uses it to refresh params.

**Contrast with `newEthAnteHandler`**: the EVM ante handler reads `EVMBlockConfig` fresh on every invocation:

```go
// evmd/ante/handler_options.go line 88
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
feemarketParams := &blockCfg.FeeMarketParams   // always current
```

This asymmetry confirms the Cosmos ante handler path is the defective one.

**Governance update path**: A governance proposal submits `MsgUpdateParams` to `x/feemarket`. `feemarket.UpdateParams` calls `k.SetParams(ctx, req.Params)`, writing the new `MinGasPrice` to the KV store. The `MinGasPriceDecorator` is never notified; it continues reading the stale pointer value for every subsequent Cosmos transaction.

---

### Impact Explanation

After governance raises `MinGasPrice` (e.g., from 0 to 1 Gwei to combat spam), the `MinGasPriceDecorator` still enforces the old value. Any Cosmos transaction whose fee satisfies the old `MinGasPrice` but not the new one passes `AnteHandle` and is committed to the chain. The governance-mandated fee floor is silently nullified for the entire Cosmos transaction path.

If `DynamicFeeChecker` is enabled (`options.DynamicFeeChecker = true`), the stale `feemarketParams.BaseFee` is used as the EIP-1559 base fee for Cosmos transactions. Because `BaseFee` is updated every block by `BeginBlock`, the checker uses the genesis-time base fee for all blocks, allowing Cosmos transactions to be committed with fees below the current block's base fee.

This fits the allowed High impact: **fee market / ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted**.

---

### Likelihood Explanation

The trigger is a governance `MsgUpdateParams` that sets a non-zero `MinGasPrice` (or any change to an already-non-zero value). This is an ordinary, unprivileged-from-the-chain's-perspective governance action. Once the proposal passes, every subsequent Cosmos transaction is checked against the stale value. No special attacker capability is required beyond submitting a normal Cosmos transaction with a fee between the old and new `MinGasPrice`.

---

### Recommendation

In `MinGasPriceDecorator.AnteHandle`, replace the cached field read with a live keeper call:

```go
// ante/cosmos/min_gas_price.go
func (mpd MinGasPriceDecorator) AnteHandle(...) {
    params := mpd.feesKeeper.GetParams(ctx)          // read live
    minGasPrice := params.MinGasPrice
    ...
}
```

Similarly, `DynamicFeeChecker` should accept a `FeeMarketKeeper` and call `GetParams(ctx)` inside the returned closure rather than closing over a captured `*feemarkettypes.Params`.

The `feemarketParams *feemarkettypes.Params` field and constructor parameter of `MinGasPriceDecorator` can then be removed entirely.

---

### Proof of Concept

1. Chain starts with `MinGasPrice = 0` (default). `newCosmosAnteHandler` is called; `feemarketParams.MinGasPrice = 0` is captured.
2. Governance passes `MsgUpdateParams` setting `MinGasPrice = 1_000_000_000` (1 Gwei). `feemarket.SetParams` writes the new value to the store.
3. Attacker submits a Cosmos `MsgSend` with `fee = 0` (or any amount below 1 Gwei × gas).
4. `MinGasPriceDecorator.AnteHandle` reads `mpd.feemarketParams.MinGasPrice` → still `0` → `minGasPrice.IsZero()` is `true` → check is **skipped entirely** (line 57 of `min_gas_price.go`).
5. Transaction passes ante handler and is committed despite violating the governance-set fee floor. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** ante/cosmos/min_gas_price.go (L36-58)
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
