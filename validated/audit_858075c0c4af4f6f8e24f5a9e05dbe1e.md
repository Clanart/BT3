### Title
Stale Fee Market Parameters Captured at Ante Handler Construction Allow Cosmos Txs to Bypass Current Base Fee Enforcement - (File: evmd/ante/handler_options.go)

### Summary
`newCosmosAnteHandler` in `evmd/ante/handler_options.go` reads `feemarketParams` from the store once at construction time (app startup) and passes a pointer to that snapshot into `NewDynamicFeeChecker`. The closure never re-reads the live store, so every Cosmos SDK transaction processed after the base fee has changed is validated against the stale initial base fee rather than the current one. An unprivileged user can craft a Cosmos tx whose fee satisfies only the stale (lower) base fee and have it accepted and committed, permanently under-collecting fees.

### Finding Description

`newCosmosAnteHandler` is called once during application initialization via `setAnteHandler` → `ante.NewAnteHandler`. At that moment it snapshots both `evmParams` and `feemarketParams` from the store:

```go
// evmd/ante/handler_options.go:179-187
evmParams := options.EvmKeeper.GetParams(ctx)
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
```

The pointer `&feemarketParams` is captured by the closure returned from `NewDynamicFeeChecker`. Inside that closure, every call to `types.GetBaseFee` reads from the captured snapshot, not from the live KV-store:

```go
// ante/evm/fee_checker.go:56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

```go
// x/evm/types/utils.go:244-254
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()   // reads struct field, not store
    ...
}
```

Meanwhile, `BeginBlock` in the feemarket module updates the base fee in the store every block:

```go
// x/feemarket/keeper/abci.go:31-38
baseFee := k.CalculateBaseFee(ctx)
...
k.SetBaseFee(ctx, baseFee)
```

`SetBaseFee` writes the new value into the params stored in the KV-store, but the snapshot held by the `NewDynamicFeeChecker` closure is never refreshed. The divergence begins at the very first block where gas usage causes the base fee to change.

By contrast, the Ethereum tx path (`newEthAnteHandler`) calls `EVMBlockConfig` on every transaction, which reads fresh params from the store each time:

```go
// evmd/ante/handler_options.go:88
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
```

```go
// x/evm/keeper/config.go:85,95
feemarketParams := k.feeMarketKeeper.GetParams(ctx)
...
baseFee = feemarketParams.GetBaseFee()
```

The same staleness affects `MinGasPriceDecorator`, which also receives `&feemarketParams` at construction:

```go
// evmd/ante/handler_options.go:198
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
```

### Impact Explanation

Any user submitting a Cosmos SDK transaction (bank send, staking, governance, IBC, etc.) after the EIP-1559 base fee has risen above its initial value can set their fee to `initialBaseFee × gasLimit` and have the transaction accepted and committed. The `DeductFeeDecorator` deducts only the `effectiveFee` returned by `NewDynamicFeeChecker`, which is computed from the stale base fee. The fee pool is permanently under-credited for every such transaction. This is a fee market ante handler bug that permits transactions with insufficient fees (relative to the current base fee) to commit, causing mis-accounting of EVM-denom funds.

### Likelihood Explanation

EIP-1559 base fee adjustment is automatic and continuous: any block whose gas usage deviates from the target causes the base fee to change. On a live chain this happens within the first few blocks. Once the base fee has moved from its genesis value, every Cosmos tx processed by `newCosmosAnteHandler` is validated against the wrong value. No special privileges, keys, or coordination are required; any user who knows the initial base fee (a public genesis parameter) can exploit this immediately.

### Recommendation

Replace the snapshot pattern with a live read inside the closure. Pass the live `FeeMarketKeeper` into `NewDynamicFeeChecker` and call `GetParams(ctx)` (or `GetBaseFee(ctx)`) on each invocation, mirroring what `newEthAnteHandler` already does via `EVMBlockConfig`:

```go
// ante/evm/fee_checker.go – proposed fix
func NewDynamicFeeChecker(ethCfg *params.ChainConfig, evmParams *types.Params,
    fmKeeper FeeMarketKeeper) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        feemarketParams := fmKeeper.GetParams(ctx)          // live read
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &feemarketParams)
        ...
    }
}
```

Apply the same fix to `MinGasPriceDecorator`.

### Proof of Concept

1. Start Ethermint with EIP-1559 enabled (`NoBaseFee = false`, London hardfork active). Initial base fee = 1 gwei.
2. Submit EVM transactions that fill blocks above the gas target for several blocks. `BeginBlock` raises the stored base fee to, say, 2 gwei.
3. Construct a Cosmos `MsgSend` transaction with `fee = 1 gwei × gasLimit` (satisfying only the stale initial base fee).
4. Submit the transaction. `NewDynamicFeeChecker` reads `feemarketParams.BaseFee = 1 gwei` from the startup snapshot, computes `effectiveFee = 1 gwei × gasLimit`, and returns no error.
5. `DeductFeeDecorator` deducts only `1 gwei × gasLimit` from the sender. The transaction commits. The fee pool receives half the fees it should have collected under the current 2 gwei base fee.

---

**Root cause files:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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
