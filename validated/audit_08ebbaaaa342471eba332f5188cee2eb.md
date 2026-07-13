### Title
Stale `feemarketParams` Snapshot in `NewDynamicFeeChecker` Allows Cosmos Transactions to Bypass the Current EIP-1559 Base Fee Requirement - (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` is constructed once at node startup with a pointer to a snapshot of `feemarketParams`. Because the EIP-1559 base fee is updated every block by the fee market module, the fee checker for Cosmos native transactions permanently uses the initial base fee from node startup rather than the current one. This allows any Cosmos transaction sender to underpay fees whenever the base fee has risen since startup, bypassing the fee market enforcement for the entire lifetime of the node.

### Finding Description

`NewDynamicFeeChecker` in `ante/evm/fee_checker.go` accepts `feemarketParams *feemarkettypes.Params` as a captured closure parameter and reads the base fee from it on every invocation:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` — the `BaseFee` field of the captured struct pointer — not the live keeper state. [1](#0-0) [2](#0-1) 

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` is called **once at app startup** (via `setAnteHandler`). It captures `feemarketParams` as a local struct value and passes `&feemarketParams` to `NewDynamicFeeChecker`:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot at startup
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [3](#0-2) 

The same stale-params pattern applies to `newLegacyCosmosAnteHandlerEip712`: [4](#0-3) 

The `MinGasPriceDecorator` in both handlers also receives `&feemarketParams`, so its `MinGasPrice` check is equally stale: [5](#0-4) 

By contrast, `newEthAnteHandler` — which handles EVM transactions — calls `EVMBlockConfig` **per invocation**, reading fresh params and the live base fee on every transaction: [6](#0-5) 

The fee checker then computes `feeCap = fee / gas` and rejects the tx only if `feeCap < baseFeeInt`. Because `baseFeeInt` is derived from the stale startup snapshot, any Cosmos tx whose fee satisfies the startup base fee passes, regardless of how high the current base fee has risen: [7](#0-6) 

The `effectiveFee` deducted from the sender is also computed from the stale base fee, so the actual amount charged is lower than the current market rate: [8](#0-7) 

The `DynamicFeeChecker` flag is set to `true` in the production app, confirming this path is active: [9](#0-8) 

### Impact Explanation

This is a fee market ante handler bug that permits Cosmos native transactions with insufficient fees (relative to the current EIP-1559 base fee) to be included in blocks. The `effectiveFee` deducted from the sender is computed against the stale startup base fee, causing systematic fee under-collection for all Cosmos transactions whenever the base fee has risen. This constitutes mis-accounting of user fees and a bypass of the fee market enforcement mechanism for the Cosmos transaction path, matching the allowed High impact: *"fee market, ante handler… bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The base fee changes every block according to EIP-1559 adjustment logic. Any node that has processed more than a handful of blocks under above-target gas usage will have a stale base fee in `NewDynamicFeeChecker`. The exploit requires no special privileges: any unprivileged user submitting a Cosmos native transaction (e.g., `MsgSend`, IBC, governance) with a fee calibrated to the startup base fee will bypass the current base fee check. The condition is continuously present for the entire lifetime of the running node.

### Recommendation

`NewDynamicFeeChecker` should not accept a pre-captured `*feemarkettypes.Params` pointer. Instead, it should accept a `FeeMarketKeeper` interface and read the current params (or base fee) from the keeper on every invocation, matching the pattern used by `newEthAnteHandler` via `EVMBlockConfig`. Alternatively, the closure should call `feemarketKeeper.GetBaseFee(ctx)` directly rather than reading from a snapshot struct.

### Proof of Concept

1. Node starts; `newCosmosAnteHandler` captures `feemarketParams.BaseFee = 1_000_000_000` (default).
2. Over subsequent blocks with above-target gas usage, `CalculateBaseFee` raises the live base fee to `2_000_000_000`.
3. Attacker submits a Cosmos `MsgSend` with `fee = 1_000_000_000 * gasLimit` (half the current market rate).
4. `NewDynamicFeeChecker` computes `feeCap = fee / gas = 1_000_000_000` and compares against `baseFeeInt = 1_000_000_000` (stale). The check `feeCap.LT(baseFeeInt)` is `false`, so the tx passes.
5. `effectiveFee = min(1_000_000_000 + tip, feeCap) * gas` is deducted — half the amount the current fee market requires.
6. The transaction is included in the block; the fee collector receives half the correct fee. The attacker has paid below the current market rate for block space, bypassing the fee market for Cosmos transactions indefinitely.

### Citations

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

**File:** ante/evm/fee_checker.go (L79-88)
```go
		gas := feeTx.GetGas()
		feeCoins := feeTx.GetFee()
		fee := feeCoins.AmountOf(denom)

		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}
```

**File:** ante/evm/fee_checker.go (L90-99)
```go
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

**File:** evmd/ante/handler_options.go (L197-199)
```go
		ante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
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

**File:** evmd/app.go (L793-795)
```go
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
		DisabledAuthzMsgs: []string{
```
