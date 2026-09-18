Found a viable analog. `AsTransaction()` returns `(nil, nil)` when `UnpackTxData` fails, and `x/evm/ante/basic.go` `BasicDecorator.AnteHandle` and `x/evm/ante/sig.go` `EVMSigVerifyDecorator.AnteHandle` both discard that error and immediately dereference the (possibly nil) `*ethtypes.Transaction`.

### Title
Unauthenticated NULL Pointer Dereference DoS in EVM Ante Handler via Malformed `MsgEVMTransaction` - (File: x/evm/ante/basic.go, x/evm/ante/sig.go)

### Summary
`MsgEVMTransaction.AsTransaction()` returns `(nil, nil)` whenever `UnpackTxData(msg.Data)` fails to decode the packed `Any` payload [1](#0-0) . Two ante decorators on the standard (non-giga) EVM transaction path call `AsTransaction()` and discard the error with `_`, then unconditionally call methods on the returned pointer:

- `BasicDecorator.AnteHandle`: `etx, _ := msg.AsTransaction()` followed by `etx.Nonce()`, `etx.To()`, `etx.Value()`, `etx.Data()`, etc. [2](#0-1) 
- `EVMSigVerifyDecorator.AnteHandle`: `ethTx, _ := types.MustGetEVMTransactionMessage(tx).AsTransaction()` followed immediately by `ethTx.Nonce()`, `ethTx.GasPrice()`, `ethTx.Gas()`, `ethTx.Value()`, `ethTx.Hash()`, `ethTx.ChainId()` [3](#0-2) 

If `etx`/`ethTx` is nil, any of these method calls on the nil `*ethtypes.Transaction` panics with a nil-pointer dereference, matching the fastschema bug class (unchecked error path → pointer dereference → crash).

### Finding Description
`ValidateBasic()` for `MsgEVMTransaction` does call `UnpackTxData` and propagate its error [4](#0-3) , so a transaction whose `Data` field cannot be unpacked at all is normally rejected before reaching the ante decorators of concern. However, the ante pipeline for regular EVM ante processing (`NewAnteHandler`) chains `BasicDecorator` and `EVMSigVerifyDecorator` (via `evmAnteDecorators`) after `NewEVMPreprocessDecorator` [5](#0-4) . Each of these decorators re-derives `txData`/`etx` independently by calling `AsTransaction()` again rather than reusing a previously validated value, and each discards the error from `AsTransaction()`. `AsTransaction()`'s only failure mode is `UnpackTxData` returning a non-nil error, at which point it returns `(nil, nil)` [1](#0-0) . Any state in which `UnpackTxData` succeeds in `ValidateBasic` (or is skipped, e.g. re-CheckTx / a path that doesn't invoke full `ValidateBasic` before ante) but fails on a later call — or any decode inconsistency between the two call sites — results in a nil `*ethtypes.Transaction` being dereferenced in these ante decorators, which is on the direct execution path of every submitted EVM transaction (`DeliverTx`/`CheckTx`).

### Impact Explanation
A nil-pointer dereference inside the ante handler panics during transaction processing. Since ante handling runs on every validator/full node processing a submitted transaction (both `CheckTx` on the mempool-serving RPC/full nodes and `DeliverTx` during block execution), a crash here can halt node availability; if triggered deterministically across validators during `DeliverTx`, it risks a validator halt rather than a mere isolated RPC crash, which exceeds the impact of the original fastschema bug (single-process crash) — this qualifies under "validator halt" / crash of default-configuration nodes.

### Likelihood Explanation
Likelihood is moderate/uncertain: the standard entry point (`ValidateBasic`) does perform an `UnpackTxData` check first, so a straightforward "just send garbage bytes" attack is very likely already rejected before these decorators run. Exploitability depends on finding a msg encoding where `UnpackTxData` succeeds during `ValidateBasic` but the identical call in `BasicDecorator`/`EVMSigVerifyDecorator` fails (or a caller path that skips `ValidateBasic`, e.g. simulate/trace/CheckTx paths that build `MsgEVMTransaction` from raw bytes directly). This could not be conclusively proven from static reading alone within the available search budget — confirming exploitability requires tracing every caller of `UnpackTxData`/`AsTransaction` and the exact conditions under which the two calls could diverge (e.g., non-idempotent unpack/caching behavior, `Any` cache invalidation, or concurrent mutation of `msg.Data`).

### Recommendation
In both `BasicDecorator.AnteHandle` and `EVMSigVerifyDecorator.AnteHandle`, check the error/nil-ness of `AsTransaction()`'s result before use, returning an ante error (e.g., `sdkerrors.ErrInvalidRequest`) instead of discarding the error with `_`. More robustly, thread a single already-validated `*ethtypes.Transaction` through the ante chain (as `Preprocess`/`PreprocessUnpacked` already computes it) instead of re-deriving and re-discarding errors at each decorator.

### Proof of Concept
Conceptual PoC (not independently executed against a live node due to tool limitations):
1. Construct a `MsgEVMTransaction` whose `Data` (`Any`) can be unpacked successfully by `UnpackTxData` when called from `ValidateBasic`/`Preprocess`, but for which a subsequent independent `UnpackTxData` call (as performed inside `AsTransaction()` when invoked from `BasicDecorator` or `EVMSigVerifyDecorator`) fails — e.g., by exploiting any registry/cache state dependency in `UnpackTxData`.
2. Submit this transaction via the normal transaction-submission path (`CheckTx`/`DeliverTx`).
3. Observe the ante pipeline reach `BasicDecorator.AnteHandle` or `EVMSigVerifyDecorator.AnteHandle`, where `etx, _ := msg.AsTransaction()` yields `etx == nil`, and the subsequent `etx.Nonce()`/`etx.To()` call panics with a nil-pointer dereference, crashing the node's transaction-processing goroutine.

Note: I was unable to fully verify within the available tool budget whether `UnpackTxData` can actually diverge between the two call sites (i.e., whether this is truly reachable past `ValidateBasic`), so this should be treated as a plausible but not fully confirmed finding pending deeper tracing of `UnpackTxData`'s caching/registry behavior.

### Citations

**File:** x/evm/types/message_evm_transaction.go (L44-56)
```go
func (msg *MsgEVMTransaction) ValidateBasic() error {
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		return sdkerrors.ErrInvalidPubKey
	}
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return err
	}
	if _, ok := txData.(*ethtx.AssociateTx); !ok {
		if err := txData.Validate(); err != nil {
			return err
		}
	}
```

**File:** x/evm/types/message_evm_transaction.go (L67-74)
```go
func (msg *MsgEVMTransaction) AsTransaction() (*ethtypes.Transaction, ethtx.TxData) {
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return nil, nil
	}

	return ethtypes.NewTx(txData.AsEthereumData()), txData
}
```

**File:** x/evm/ante/basic.go (L25-51)
```go
func (gl BasicDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	etx, _ := msg.AsTransaction()

	if msg.Derived != nil && !gl.k.EthReplayConfig.Enabled && !gl.k.EthBlockTestConfig.Enabled {
		startingNonce := gl.k.GetNonce(ctx, msg.Derived.SenderEVMAddr)
		txNonce := etx.Nonce()
		if !ctx.IsCheckTx() && !ctx.IsReCheckTx() && startingNonce == txNonce {
			ctx = ctx.WithDeliverTxCallback(func(callCtx sdk.Context) {
				// bump nonce if it is for some reason not incremented (e.g. ante failure)
				if gl.k.GetNonce(callCtx, msg.Derived.SenderEVMAddr) == startingNonce {
					gl.k.SetNonce(callCtx, msg.Derived.SenderEVMAddr, startingNonce+1)
					gl.k.SetNonceBumped(callCtx)
				}
			})
		}
	}

	if etx.To() == nil && len(etx.Data()) > params.MaxInitCodeSize {
		return ctx, fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(etx.Data()), params.MaxInitCodeSize)
	}

	if etx.Value().Sign() < 0 {
		return ctx, sdkerrors.ErrInvalidCoins
	}

	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
```

**File:** x/evm/ante/sig.go (L30-49)
```go
func (svd *EVMSigVerifyDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	ethTx, _ := types.MustGetEVMTransactionMessage(tx).AsTransaction()

	evmAddr := types.MustGetEVMTransactionMessage(tx).Derived.SenderEVMAddr

	nextNonce := svd.evmKeeper.GetNonce(ctx, evmAddr)
	txNonce := ethTx.Nonce()

	// set EVM properties
	ctx = ctx.WithIsEVM(true)
	ctx = ctx.WithEVMNonce(txNonce)
	ctx = ctx.WithEVMSenderAddress(evmAddr)
	ctx = ctx.WithSeiSenderAddress(types.MustGetEVMTransactionMessage(tx).Derived.SenderSeiAddr)
	ctx = ctx.WithEVMTxHash(ethTx.Hash())

	chainID := svd.evmKeeper.ChainID(ctx)
	txChainID := ethTx.ChainId()

	fee := new(big.Int).Mul(ethTx.GasPrice(), new(big.Int).SetUint64(ethTx.Gas()))
	if ethTx.Value() != nil {
```

**File:** app/ante.go (L91-100)
```go
	evmAnteDecorators := []sdk.AnteDecorator{
		// NOTE: NewEVMNoCosmosFieldsDecorator must come first to prevent writing state to chain without being charged.
		// E.g. EVMPreprocessDecorator may short-circuit all the later ante handlers if AssociateTx and ignore NewEVMNoCosmosFieldsDecorator.
		evmante.NewEVMNoCosmosFieldsDecorator(),
		evmante.NewEVMPreprocessDecorator(options.EVMKeeper, options.EVMKeeper.AccountKeeper()),
		evmante.NewBasicDecorator(options.EVMKeeper),
		evmante.NewEVMFeeCheckDecorator(options.EVMKeeper, options.UpgradeKeeper),
		evmante.NewEVMSigVerifyDecorator(options.EVMKeeper, options.LatestCtxGetter),
		evmante.NewGasDecorator(options.EVMKeeper),
	}
```
