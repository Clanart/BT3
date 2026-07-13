### Title
Stale Base Fee Captured at Ante Handler Construction Bypasses EIP-1559 Fee Market for Cosmos Native Transactions — (`evmd/ante/handler_options.go`)

---

### Summary

`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` fetch `feemarketParams` (including `BaseFee`) once at construction time and pass a pointer to the captured local copy into `NewDynamicFeeChecker`. Because the ante handler is built once at app initialization, the base fee used for all subsequent Cosmos-native transaction fee validation is permanently frozen at the genesis value, regardless of how many blocks of EIP-1559 adjustment have occurred.

---

### Finding Description

In `evmd/ante/handler_options.go`, both `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` snapshot `feemarketParams` at construction time:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)          // snapshot at init
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx) // snapshot at init
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    return sdk.ChainAnteDecorators(decorators...)
}
``` [1](#0-0) 

The returned `sdk.AnteHandler` closure is registered once on the app and reused for every subsequent transaction. Inside `NewDynamicFeeChecker`, the fee check reads `BaseFee` from the captured pointer:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads directly from `feemarketParams.BaseFee`:

```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()
    ...
}
``` [3](#0-2) 

Meanwhile, `feemarket.BeginBlock` updates the live base fee in the KVStore every block:

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
    ...
}
``` [4](#0-3) 

`SetBaseFee` writes the updated value into the KVStore params:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
    ...
}
``` [5](#0-4) 

The local `feemarketParams` copy captured in the closure is **never refreshed**. The KVStore update is invisible to the fee checker.

**Contrast with the EVM ante handler**, which correctly fetches fresh params per transaction:

```go
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, ...)
        ...
        feemarketParams := &blockCfg.FeeMarketParams
        baseFee := blockCfg.BaseFee
        ...
    }
}
``` [6](#0-5) 

`EVMBlockConfig` reads fresh params from the KVStore on every call (with object-store caching scoped to the current block): [7](#0-6) 

The asymmetry is clear: EVM transactions always see the current base fee; Cosmos-native transactions always see the genesis base fee.

---

### Impact Explanation

When the EIP-1559 base fee rises above its genesis value (which happens whenever blocks are consistently above the gas target), any user submitting a Cosmos-native transaction (e.g., `MsgSend`, `MsgDelegate`, `MsgVote`) with fees priced at the genesis base fee will pass `NewDynamicFeeChecker`'s `feeCap < baseFeeInt` check:

```go
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
``` [8](#0-7) 

Because `baseFeeInt` is frozen at the genesis value, the check passes even though the transaction's fee is below the live required base fee. The transaction is included in a block and committed, with the fee collector receiving less than the protocol-mandated minimum. This is a fee mis-accounting bug: valid user funds/fees are mis-accounted and invalid (under-priced) transactions commit.

**Allowed impact matched:** *"High. EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

- Requires no special privileges — any user submitting a Cosmos-native transaction triggers this path.
- The base fee rises automatically whenever blocks are above the gas target, which is a normal network condition.
- The `DynamicFeeChecker` flag must be enabled (`options.DynamicFeeChecker = true`), which is the intended production configuration for EIP-1559-enabled chains.
- The bug is permanent from block 1 onward; it does not require any special timing or race condition.

---

### Recommendation

Replace the construction-time snapshot with a live lookup inside the returned closure. Mirror the pattern used by `newEthAnteHandler`: call `options.FeeMarketKeeper.GetParams(ctx)` (or `options.EvmKeeper.EVMBlockConfig(ctx, ...)`) inside the per-transaction closure so that `BaseFee` is always read from the current KVStore state.

```go
func newCosmosAnteHandler(options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
    // Remove ctx parameter; fetch params inside the returned closure instead.
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        evmParams := options.EvmKeeper.GetParams(ctx)
        feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
        // build decorators with fresh params and run them
        ...
    }
}
```

Alternatively, pass a `FeeMarketKeeper` reference into `NewDynamicFeeChecker` and call `keeper.GetBaseFee(ctx)` inside the closure, consistent with how `newEthAnteHandler` uses `EVMBlockConfig`.

---

### Proof of Concept

1. Chain starts with genesis `BaseFee = 1_000_000_000` (1 Gwei). `newCosmosAnteHandler` is called once; `feemarketParams.BaseFee = 1_000_000_000` is captured.
2. Over several blocks with high gas usage, `feemarket.BeginBlock` raises the live base fee to `2_000_000_000` (2 Gwei) in the KVStore.
3. Attacker submits a `MsgSend` with `gasPrice = 1_000_000_001` (just above genesis base fee, below live base fee).
4. `NewDynamicFeeChecker` computes `feeCap = 1_000_000_001`, `baseFeeInt = 1_000_000_000` (stale). Check `feeCap.LT(baseFeeInt)` → false → transaction passes.
5. The transaction is included and committed. The fee collector receives `1_000_000_001 * gas` instead of the required `2_000_000_000 * gas`. The fee market is bypassed for all Cosmos-native transactions for the lifetime of the node process.

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

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

**File:** ante/evm/fee_checker.go (L86-88)
```go
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

**File:** x/feemarket/keeper/abci.go (L30-51)
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
```

**File:** x/feemarket/keeper/params.go (L72-78)
```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
	params := k.GetParams(ctx)
	params.BaseFee = ethermint.SaturatedNewInt(baseFee)
	err := k.SetParams(ctx, params)
	if err != nil {
		return
	}
```

**File:** x/evm/keeper/config.go (L75-100)
```go
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
