### Title
Stale Fee-Market Parameter Snapshot in Cosmos Ante Handler Bypasses Current Base-Fee Enforcement — (File: `evmd/ante/handler_options.go`, `evmd/ante/evm_handler.go`)

---

### Summary

`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` snapshot `feemarketParams` once at construction time and pass the frozen pointer into `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Every Cosmos (non-EVM) transaction processed afterward is fee-checked against that stale snapshot, not the live on-chain `BaseFee` that `BeginBlock` updates every block. The EVM ante handler (`newEthAnteHandler`) does not share this flaw — it re-reads params fresh per transaction via `EVMBlockConfig`.

---

### Finding Description

**Root cause — `newCosmosAnteHandler` (and its legacy EIP-712 sibling):**

```go
// evmd/ante/handler_options.go  lines 178-212
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams        := options.EvmKeeper.GetParams(ctx)       // snapshot at construction
    feemarketParams  := options.FeeMarketKeeper.GetParams(ctx) // snapshot at construction
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams) // stale ptr
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams), // stale ptr
```

`feemarketParams` is a **local variable**. `&feemarketParams` is captured by both `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Neither decorator ever re-reads from the keeper; they dereference the captured pointer directly:

```go
// ante/evm/fee_checker.go  line 56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams) // feemarketParams never updated

// ante/cosmos/min_gas_price.go  line 54
minGasPrice := mpd.feemarketParams.MinGasPrice // stale snapshot
```

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` → `feemarketParams.BaseFee.BigInt()` — the value frozen at construction time.

The same pattern appears in `newLegacyCosmosAnteHandlerEip712`:

```go
// evmd/ante/evm_handler.go  lines 29-37
evmParams        := options.EvmKeeper.GetParams(ctx)
feemarketParams  := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
```

**Contrast — `newEthAnteHandler` (correct pattern):**

```go
// evmd/ante/handler_options.go  lines 86-96
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
feemarketParams := &blockCfg.FeeMarketParams   // re-read from live store per transaction
baseFee         := blockCfg.BaseFee
```

`EVMBlockConfig` reads from the live KV store on every transaction, so EVM transactions always see the current `BaseFee`.

**How `BaseFee` becomes stale:**

`feemarket.BeginBlock` calls `k.SetBaseFee(ctx, baseFee)` → `k.SetParams(ctx, params)` which writes the updated `BaseFee` to the KV store. The in-memory local variable captured by the Cosmos ante handler is never touched by this write path.

```go
// x/feemarket/keeper/abci.go  lines 30-51
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)   // writes to KV store only; captured local var unchanged
```

---

### Impact Explanation

Every Cosmos (non-EVM) transaction — governance votes, IBC transfers, staking operations, bank sends — is fee-validated against the `BaseFee` and `MinGasPrice` that were current when the ante handler was constructed (app startup / last node restart). After the fee market adjusts upward due to sustained high block utilization, an attacker can submit Cosmos transactions with fees calibrated to the stale (lower) base fee and have them accepted and committed. Validators receive less fee revenue than the protocol mandates, and the fee-market spam-prevention invariant is broken for the entire Cosmos transaction path.

This matches the allowed High impact: *"fee market, ante handler… bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

- The `BaseFee` changes every block (up to ±12.5% per block with default `BaseFeeChangeDenominator = 8`). After sustained high utilization the live base fee can be orders of magnitude above the startup value.
- Any unprivileged user can submit a Cosmos transaction with a fee set to `startupBaseFee * gas`, which will pass the stale checker but be below the live required fee.
- No special privileges, keys, or network access beyond normal transaction submission are required.
- The discrepancy is permanent until the node is restarted (at which point the snapshot is refreshed to the current value, only to become stale again immediately).

---

### Recommendation

Replace the snapshot pattern in `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` with per-transaction live reads, mirroring `newEthAnteHandler`:

1. Remove the upfront `GetParams` calls and the stale pointer captures.
2. In `NewDynamicFeeChecker`, read `feemarketParams` from the keeper inside the returned closure using `ctx` (the per-transaction context), exactly as `EVMBlockConfig` does for EVM transactions.
3. In `MinGasPriceDecorator.AnteHandle`, replace `mpd.feemarketParams.MinGasPrice` with `mpd.feesKeeper.GetParams(ctx).MinGasPrice` (the keeper reference is already stored in the struct).

---

### Proof of Concept

1. At node startup the `BaseFee` is `B₀` (e.g., 765,625,000 aevmos).
2. The network runs at >50% block capacity for N blocks; `BeginBlock` raises `BaseFee` to `B_N` (e.g., 10 × B₀).
3. An attacker submits a Cosmos `MsgSend` with `fee = B₀ × gasLimit`.
4. `newCosmosAnteHandler`'s `NewDynamicFeeChecker` evaluates `feeCap = fee / gas = B₀` against `baseFee = feemarketParams.BaseFee = B₀` (stale snapshot) → `feeCap >= baseFee` → **passes**.
5. The live `feemarket.GetBaseFee(ctx)` would return `B_N = 10 × B₀`, so the correct check would reject the transaction.
6. The transaction is committed with a fee 10× below the protocol-mandated minimum. [1](#0-0) [2](#0-1) 
<cite repo="Annirich/ethermint--017" path="ante/e

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
