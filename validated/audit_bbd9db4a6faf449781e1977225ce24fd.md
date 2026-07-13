### Title
Stale `feemarketParams` Captured at Construction Time in `NewDynamicFeeChecker` Enables Fee Market Bypass for Legacy EIP-712 Cosmos Transactions - (File: `ante/evm/fee_checker.go`, `evmd/ante/evm_handler.go`)

### Summary

`newLegacyCosmosAnteHandlerEip712` snapshots `feemarketParams` (including `BaseFee`) once at construction time and passes a pointer to that stale copy into `NewDynamicFeeChecker`. Because the EIP-1559 base fee is recalculated and persisted every block, the fee checker permanently uses an outdated base fee for the entire lifetime of the node, allowing legacy EIP-712 Cosmos transactions to be accepted and committed with fees below the current on-chain base fee.

### Finding Description

In `evmd/ante/evm_handler.go`, the deprecated `newLegacyCosmosAnteHandlerEip712` reads params once from the keeper at construction time:

```go
evmParams := options.EvmKeeper.GetParams(ctx)
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
```

`GetParams` returns a **value copy** of the params struct. `&feemarketParams` is a pointer to that local copy. This pointer is captured in the closure returned by `NewDynamicFeeChecker`:

```go
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params, feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` → `feemarketParams.BaseFee.BigInt()`, which is the base fee value frozen at construction time. Every block, `feemarket.BeginBlock` calls `SetBaseFee` → `SetParams`, updating the on-chain base fee in the KV store. But the captured `feemarketParams` copy is never refreshed.

By contrast, the main EVM ante handler (`newEthAnteHandler`) correctly reads fresh params on every transaction via `EVMBlockConfig`:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
baseFee := blockCfg.BaseFee
```

`EVMBlockConfig` calls `k.feeMarketKeeper.GetParams(ctx)` live from the store on each invocation.

### Impact Explanation

The `NewDynamicFeeChecker` enforces `feeCap >= baseFee` and computes `effectiveFee = effectivePrice * gas` using the stale base fee. If the on-chain base fee has risen since construction (e.g., due to sustained high block utilization, which increases the base fee up to 12.5% per block), any legacy EIP-712 Cosmos transaction with `feeCap` between `stale_baseFee` and `current_baseFee` will:

1. Pass the `feeCap.LT(baseFeeInt)` check using the stale (lower) base fee.
2. Have an effective fee deducted that is lower than what the current base fee requires.
3. Be committed to the chain with under-priced fees, bypassing the EIP-1559 fee market.

This is a fee market ante handler bug that permits invalid transactions (relative to the current base fee) to commit and causes user funds/fees to be mis-accounted — matching the High impact scope.

### Likelihood Explanation

The entry path is unprivileged: any user can submit a legacy EIP-712 Cosmos transaction. The condition is that the on-chain base fee has increased since the node last constructed the ante handler (i.e., since startup or last restart). Given that base fee adjusts every block and nodes run continuously, the stale gap grows monotonically. After sustained high-traffic periods, the stale base fee can be significantly below the current base fee, making the bypass window wide and easy to exploit.

### Recommendation

`NewDynamicFeeChecker` should not accept pre-captured `feemarketParams`. Instead, it should read fresh params from the keeper on every invocation, mirroring the pattern used in `newEthAnteHandler`:

```go
func NewDynamicFeeChecker(feeMarketKeeper FeeMarketKeeper, ethCfg *params.ChainConfig, evmParams *types.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        feemarketParams := feeMarketKeeper.GetParams(ctx)
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &feemarketParams)
        ...
    }
}
```

Similarly, `newLegacyCosmosAnteHandlerEip712` should not snapshot `feemarketParams` at construction time for use in fee checking.

### Proof of Concept

1. Node starts; `newLegacyCosmosAnteHandlerEip712` is constructed with `feemarketParams.BaseFee = 1_000_000_000` (1 Gwei).
2. Network sustains high block utilization for 6 blocks; base fee increases ~12.5%/block to ~2_000_000_000 (2 Gwei) on-chain.
3. Attacker submits a legacy EIP-712 Cosmos transaction with `gasFeeCap = 1_500_000_000` (1.5 Gwei).
4. `NewDynamicFeeChecker` checks `feeCap (1.5 Gwei) >= stale_baseFee (1 Gwei)` → passes.
5. Effective fee is computed as `min(1 Gwei + tip, 1.5 Gwei) * gas` — well below the current 2 Gwei requirement.
6. `authante.NewDeductFeeDecorator` deducts the under-priced fee and the transaction commits.
7. The attacker has bypassed the current fee market floor, paying ~25–50% less than required. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** x/evm/keeper/config.go (L75-135)
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
	time := ctx.BlockHeader().Time
	var blockTime uint64
	if !time.IsZero() {
		blockTime, err = ethermint.SafeUint64(time.Unix())
		if err != nil {
			return nil, err
		}
	}
	blockNumber := big.NewInt(ctx.BlockHeight())
	rules := ethCfg.Rules(blockNumber, ethCfg.MergeNetsplitBlock != nil, blockTime)

	// Build the default precompile set once per block.
	contracts := make(map[common.Address]vm.PrecompiledContract)
	for addr, c := range vm.DefaultPrecompiles(rules) {
		contracts[addr] = c
	}

	var zero common.Hash
	cfg := &EVMBlockConfig{
		Params:             params,
		FeeMarketParams:    feemarketParams,
		ChainConfig:        ethCfg,
		CoinBase:           coinbase,
		BaseFee:            baseFee,
		Difficulty:         new(big.Int),
		Random:             &zero,
		BlobBaseFee:        new(big.Int),
		BlockNumber:        blockNumber,
		BlockTime:          blockTime,
		Rules:              rules,
		DefaultPrecompiles: contracts,
	}
	objStore.Set(types.KeyPrefixObjectParams, cfg)
	return cfg, nil
}
```

**File:** x/feemarket/keeper/abci.go (L30-52)
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
}
```

**File:** x/feemarket/keeper/params.go (L57-69)
```go
func (k Keeper) GetBaseFee(ctx sdk.Context) *big.Int {
	params := k.GetParams(ctx)
	if params.NoBaseFee {
		return nil
	}

	baseFee := params.BaseFee.BigInt()
	if baseFee == nil || baseFee.Sign() == 0 {
		// try v1 format
		return k.GetBaseFeeV1(ctx)
	}
	return baseFee
}
```
