### Title
Stale `feemarketParams` Snapshot in `MinGasPriceDecorator` Bypasses Governance-Updated `MinGasPrice` for Cosmos Transactions — (`evmd/ante/handler_options.go`, `ante/cosmos/min_gas_price.go`)

---

### Summary

`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` snapshot `feemarketParams` once at ante-handler construction time and pass a pointer to that snapshot into `MinGasPriceDecorator`. The decorator then enforces the stale `MinGasPrice` value for every subsequent Cosmos transaction, ignoring any on-chain governance update to the parameter. An unprivileged user can submit Cosmos transactions with fees below the governance-mandated minimum and have them accepted and committed.

---

### Finding Description

`newCosmosAnteHandler` fetches fee-market parameters exactly once, at construction time, from the keeper:

```go
// evmd/ante/handler_options.go:180
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot – never refreshed
...
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

The identical pattern appears in the legacy EIP-712 handler:

```go
// evmd/ante/evm_handler.go:30
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [2](#0-1) 

`MinGasPriceDecorator` stores this pointer and reads `MinGasPrice` exclusively from it during every `AnteHandle` call:

```go
// ante/cosmos/min_gas_price.go:54
minGasPrice := mpd.feemarketParams.MinGasPrice
``` [3](#0-2) 

The `feesKeeper` field stored in the struct is **never consulted** inside `AnteHandle`; it exists but is unused for the actual enforcement. [4](#0-3) 

By contrast, the EVM ante handler (`newEthAnteHandler`) is a closure that calls `options.EvmKeeper.EVMBlockConfig(ctx, …)` on **every transaction**, fetching live params from state each time:

```go
// evmd/ante/handler_options.go:88-95
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
feemarketParams := &blockCfg.FeeMarketParams
``` [5](#0-4) 

This asymmetry means EVM transactions always see the current `MinGasPrice`, while Cosmos transactions see the value that was in state when the ante handler was first built (app startup).

The same stale snapshot is also passed to `NewDynamicFeeChecker`, affecting the fee cap calculation for Cosmos transactions when `DynamicFeeChecker` is enabled:

```go
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [6](#0-5) 

---

### Impact Explanation

After a governance proposal successfully raises `MinGasPrice` (stored via `keeper.SetParams`), the `MinGasPriceDecorator` for Cosmos transactions continues to enforce the **old, lower** value. Any user can craft a Cosmos transaction with a fee between the old and new minimum, pass the stale ante-handler check, and have the transaction committed to a block. This is a direct ante-handler bug that permits transactions that are invalid under the current on-chain rules to commit — matching the **High** impact category: *"ante handler bug that permits invalid transactions to commit."*

The `DynamicFeeChecker` stale snapshot compounds this: fee deduction calculations for Cosmos txs also use outdated parameters, potentially mis-accounting user fees.

---

### Likelihood Explanation

Governance updates to `MinGasPrice` are a normal, permissionless-to-propose chain operation. Once a proposal passes, the parameter is updated in the KV store immediately, but the running ante handler retains the old snapshot indefinitely until the node process is restarted. On a live chain, nodes are rarely restarted, so the window of exposure is effectively the entire remaining uptime of the chain after the governance change. Any user who observes the governance update can immediately exploit the stale check.

---

### Recommendation

Replace the cached snapshot with a live read inside `AnteHandle`. The `feesKeeper` is already stored in the struct for exactly this purpose — use it:

```go
// ante/cosmos/min_gas_price.go – AnteHandle
func (mpd MinGasPriceDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (newCtx sdk.Context, err error) {
    ...
    // Replace: minGasPrice := mpd.feemarketParams.MinGasPrice
    minGasPrice := mpd.feesKeeper.GetParams(ctx).MinGasPrice
    ...
}
```

Similarly, `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` should not snapshot `feemarketParams` at construction time for use in `MinGasPriceDecorator`. The `DynamicFeeChecker` should also be refactored to read live params, or the checker should be rebuilt per-block.

---

### Proof of Concept

1. Chain launches with `MinGasPrice = 0`.
2. Governance proposal raises `MinGasPrice` to `1000000000` (1 Gwei). Proposal passes; `keeper.SetParams` writes the new value to the KV store.
3. A user submits a Cosmos `MsgSend` with `gasPrice = 1` (below the new minimum).
4. `MinGasPriceDecorator.AnteHandle` reads `mpd.feemarketParams.MinGasPrice`, which is still `0` (the snapshot from app startup).
5. The check `if minGasPrice.IsZero()` short-circuits at line 57, and the transaction is accepted and committed — bypassing the governance-mandated fee floor entirely. [7](#0-6)

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

**File:** evmd/ante/evm_handler.go (L29-47)
```go
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
```

**File:** ante/cosmos/min_gas_price.go (L36-59)
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
```
