### Title
Stale `feemarketParams` Captured at Ante-Handler Construction Causes Cosmos-Native Transactions to Bypass Current Base-Fee Enforcement — (`evmd/ante/handler_options.go`)

---

### Summary

`newCosmosAnteHandler` (and `newLegacyCosmosAnteHandlerEip712`) snapshot `feemarketParams` — including `BaseFee` — **once at construction time** and pass a pointer to that snapshot into `NewDynamicFeeChecker`. Because the base fee is recalculated every block, the fee checker used for all Cosmos-native transactions operates against a permanently stale base fee, allowing under-priced transactions to pass ante-handler validation indefinitely.

---

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` fetches params from the keeper once and captures a pointer to the local copy:

```go
// evmd/ante/handler_options.go  lines 179-187
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams        := options.EvmKeeper.GetParams(ctx)       // snapshot
    feemarketParams  := options.FeeMarketKeeper.GetParams(ctx) // snapshot
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

The returned `sdk.AnteHandler` closure is registered once at app startup and reused for every subsequent block. The pointer `&feemarketParams` points to a Go heap-escaped local whose value is **never refreshed**.

Inside `NewDynamicFeeChecker`, every transaction evaluation calls:

```go
// ante/evm/fee_checker.go  line 56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` — the stale snapshot — rather than querying the live store:

```go
// x/evm/types/utils.go  lines 244-254
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    if !IsLondon(ethCfg, height) { return nil }
    baseFee := feemarketParams.GetBaseFee()   // reads the captured snapshot
    ...
    return baseFee
}
``` [3](#0-2) 

The same stale pointer is also passed to `NewMinGasPriceDecorator`:

```go
// evmd/ante/handler_options.go  line 198
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [4](#0-3) 

**Contrast with `newEthAnteHandler`**, which correctly fetches a fresh `blockCfg` on every invocation:

```go
// evmd/ante/handler_options.go  lines 88-95
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
feemarketParams := &blockCfg.FeeMarketParams
baseFee         := blockCfg.BaseFee
``` [5](#0-4) 

The same construction-time snapshot bug exists in `newLegacyCosmosAnteHandlerEip712`:

```go
// evmd/ante/evm_handler.go  lines 29-37
func newLegacyCosmosAnteHandlerEip712(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams       := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [6](#0-5) 

The live `CalculateBaseFee` / `SetBaseFee` path updates `params.BaseFee` in the KV store every block:

```go
// x/feemarket/keeper/eip1559.go  lines 32-104
func (k Keeper) CalculateBaseFee(ctx sdk.Context) *big.Int { ... }
``` [7](#0-6) 

But the ante handler for Cosmos-native transactions never reads from that updated store; it always reads the snapshot.

---

### Impact Explanation

When the network is busy and the base fee rises above the snapshot value, any Cosmos-native transaction (including EIP-712 / legacy Cosmos SDK txs) whose `feeCap` satisfies the **stale** (lower) base fee will pass `NewDynamicFeeChecker` and be committed, even though it underpays relative to the **current** base fee. The effective fee deducted from the sender is computed from the stale base fee, so the protocol permanently under-collects fees for every such transaction. This is a direct fee mis-accounting bug in the ante handler path.

Conversely, if the base fee has dropped since construction, valid transactions are incorrectly rejected — a liveness impact for Cosmos-native senders.

---

### Likelihood Explanation

The base fee changes every block whenever gas usage deviates from the target. On any chain with variable load the snapshot diverges from the live value within minutes of startup. No special privileges are required: any unprivileged user submitting a Cosmos-native (non-EVM) transaction triggers the path. The attacker simply submits a transaction with a `feeCap` between the stale base fee and the current (higher) base fee; it passes CheckTx and DeliverTx/FinalizeBlock because both use the same stale checker.

---

### Recommendation

Remove the construction-time snapshot. `NewDynamicFeeChecker` should accept the `FeeMarketKeeper` interface and call `keeper.GetParams(ctx)` (or `keeper.GetBaseFee(ctx)`) inside the returned closure, mirroring the pattern already used in `newEthAnteHandler`:

```go
// Correct pattern (already used for EVM txs):
blockCfg, _ := evmKeeper.EVMBlockConfig(ctx, evmKeeper.ChainID())
baseFee := blockCfg.BaseFee
```

For `NewDynamicFeeChecker`, replace the `*feemarkettypes.Params` parameter with a `FeeMarketKeeper` interface and fetch live params inside the closure on every call.

---

### Proof of Concept

1. Chain starts; `newCosmosAnteHandler` is called once. Snapshot: `BaseFee = 1_000_000_000`.
2. Network load is high for N blocks; `CalculateBaseFee` raises the live `BaseFee` to `2_000_000_000`.
3. Attacker submits a Cosmos-native tx with `feeCap = 1_500_000_000` (above snapshot, below live).
4. `NewDynamicFeeChecker` evaluates `feeCap (1.5e9) >= baseFee (1.0e9 — stale)` → passes.
5. `DeductFeeDecorator` deducts fees computed from the stale base fee.
6. Transaction commits; attacker paid 25 % less than the protocol-mandated base fee. [8](#0-7) [9](#0-8)

### Citations

**File:** evmd/ante/handler_options.go (L88-95)
```go
		blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
		if err != nil {
			return ctx, errorsmod.Wrap(errortypes.ErrLogic, err.Error())
		}
		evmParams := &blockCfg.Params
		evmDenom := evmParams.EvmDenom
		feemarketParams := &blockCfg.FeeMarketParams
		baseFee := blockCfg.BaseFee
```

**File:** evmd/ante/handler_options.go (L178-211)
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
```

**File:** ante/evm/fee_checker.go (L56-56)
```go
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

**File:** x/feemarket/keeper/eip1559.go (L32-104)
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

	// get the block gas used and the base fee values for the parent block.
	// NOTE: this is not the parent's base fee but the current block's base fee,
	// as it is retrieved from the transient store, which is committed to the
	// persistent KVStore after EndBlock (ABCI Commit).
	parentBaseFee := params.BaseFee.BigInt()
	if parentBaseFee == nil {
		return nil
	}

	parentGasUsed := k.GetBlockGasWanted(ctx)

	// NOTE: a MaxGas equal to -1 means that block gas is unlimited
	if consParams.Block == nil || consParams.Block.MaxGas <= -1 {
		panic(fmt.Sprintf("get invalid consensus params: %s", consParams))
	}
	gasLimit := big.NewInt(consParams.Block.MaxGas)
	// CONTRACT: ElasticityMultiplier cannot be 0 as it's checked in the params
	// validation
	parentGasTargetBig := new(big.Int).Div(gasLimit, new(big.Int).SetUint64(uint64(params.ElasticityMultiplier)))
	if !parentGasTargetBig.IsUint64() {
		return nil
	}

	parentGasTarget := parentGasTargetBig.Uint64()
	baseFeeChangeDenominator := new(big.Int).SetUint64(uint64(params.BaseFeeChangeDenominator))

	// If the parent gasUsed is the same as the target, the baseFee remains
	// unchanged.
	if parentGasUsed == parentGasTarget {
		return new(big.Int).Set(parentBaseFee)
	}

	if parentGasUsed > parentGasTarget {
		// If the parent block used more gas than its target, the baseFee should
		// increase.
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - parentGasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, parentGasTargetBig)
		baseFeeDelta := ethermint.BigMax(
			x.Div(y, baseFeeChangeDenominator),
			common.Big1,
		)

		return x.Add(parentBaseFee, baseFeeDelta)
	}

	// Otherwise if the parent block used less gas than its target, the baseFee
	// should decrease.
	gasUsedDelta := new(big.Int).SetUint64(parentGasTarget - parentGasUsed)
	x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
	y := x.Div(x, parentGasTargetBig)
	baseFeeDelta := x.Div(y, baseFeeChangeDenominator)

	// Set global min gas price as lower bound of the base fee, transactions below
	// the min gas price don't even reach the mempool.
	minGasPrice := params.MinGasPrice.TruncateInt().BigInt()
	return ethermint.BigMax(x.Sub(parentBaseFee, baseFeeDelta), minGasPrice)
```
