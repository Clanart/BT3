### Title
Stale Fee Market Parameters in `NewDynamicFeeChecker` Allow Cosmos Native Transactions to Bypass the Live Base Fee - (File: ante/evm/fee_checker.go)

---

### Summary

`NewDynamicFeeChecker` captures `feemarketParams` at construction time inside `newCosmosAnteHandler`. Because `params.BaseFee` is updated in the KV store on every block by `SetBaseFee` / `BeginBlock`, the stale snapshot means the Cosmos-native-tx fee checker always enforces the **genesis** base fee rather than the current one. EVM transactions are unaffected because `newEthAnteHandler` reads fresh params on every call via `EVMBlockConfig`.

---

### Finding Description

**Construction-time capture in `newCosmosAnteHandler`**

In `evmd/ante/handler_options.go` lines 179–187, `newCosmosAnteHandler` reads params once from the keeper and passes a pointer to the local copy into `NewDynamicFeeChecker`:

```go
evmParams := options.EvmKeeper.GetParams(ctx)
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

The same pattern appears in `newLegacyCosmosAnteHandlerEip712`: [2](#0-1) 

**Stale params used on every transaction**

`NewDynamicFeeChecker` closes over the pointer and calls `types.GetBaseFee` with the stale `feemarketParams` on every transaction:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
...
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, ...)
}
``` [3](#0-2) 

`GetBaseFee` reads `feemarketParams.BaseFee` directly: [4](#0-3) 

**Live base fee is updated every block**

`SetBaseFee` writes the new base fee into the KV store on every `BeginBlock`, but the stale local copy captured in the closure is never refreshed: [5](#0-4) [6](#0-5) 

**Contrast: EVM transactions read fresh params**

`newEthAnteHandler` does not capture params at construction time; it calls `EVMBlockConfig` inside the closure on every transaction, obtaining the current `BaseFee` and `FeeMarketParams`: [7](#0-6) 

---

### Impact Explanation

When the live base fee rises above the genesis value (normal behaviour under any sustained load), Cosmos native transactions processed by `NewDynamicFeeChecker` are checked against the lower genesis base fee. A user can submit a Cosmos native transaction whose `feeCap` satisfies the stale genesis base fee but is below the current live base fee. The ante handler accepts and commits the transaction, mis-accounting fees and admitting transactions that should be rejected. This matches the allowed High impact: *"fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

The base fee diverges from its genesis value on every block where gas usage differs from the gas target — i.e., under any real network load. No privileged action is required: any unprivileged user can submit a Cosmos native transaction with a `feeCap` equal to the genesis base fee. The only prerequisite is that `options.DynamicFeeChecker = true`, which is the intended production configuration for chains that want EIP-1559 fee enforcement on Cosmos-side transactions.

---

### Recommendation

`NewDynamicFeeChecker` should not accept pre-fetched `*feemarkettypes.Params`. Instead, it should accept a `FeeMarketKeeper` interface and call `GetParams(ctx)` inside the closure on every invocation, mirroring the pattern used by `newEthAnteHandler` via `EVMBlockConfig`. The same fix applies to `NewMinGasPriceDecorator`, which also receives `&feemarketParams` at construction time.

---

### Proof of Concept

1. Chain launches with genesis `BaseFee = 1_000_000_000` (1 gwei), `NoBaseFee = false`, London HF active.
2. `newCosmosAnteHandler` is called once at app startup; the closure captures `feemarketParams.BaseFee = 1_000_000_000`.
3. Sustained network activity causes `CalculateBaseFee` / `SetBaseFee` to raise the live base fee to `2_000_000_000` (2 gwei) after several blocks. The KV store now holds `params.BaseFee = 2_000_000_000`.
4. The stale closure still holds `feemarketParams.BaseFee = 1_000_000_000`.
5. Attacker submits a Cosmos native tx with `fee / gas = 1_000_000_000`.
6. `NewDynamicFeeChecker` evaluates `feeCap (1e9) >= baseFee (1e9 stale)` → passes.
7. The tx is committed despite paying only half the required base fee, bypassing the live fee market enforcement.

### Citations

**File:** evmd/ante/handler_options.go (L87-96)
```go
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

**File:** evmd/ante/handler_options.go (L179-187)
```go
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker ante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
```

**File:** evmd/ante/evm_handler.go (L29-37)
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
```

**File:** ante/evm/fee_checker.go (L56-88)
```go
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
