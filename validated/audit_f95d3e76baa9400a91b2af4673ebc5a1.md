### Title
Stale `feemarketParams` Snapshot in Cosmos Ante Handler Causes Persistent Fee Mis-Accounting - (`evmd/ante/handler_options.go`, `ante/evm/fee_checker.go`, `ante/cosmos/min_gas_price.go`)

### Summary

`newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712` capture `feemarketParams` as a value-copy snapshot at construction time and pass a pointer to that snapshot into both `NewDynamicFeeChecker` and `NewMinGasPriceDecorator`. Because the base fee is updated every block via `BeginBlock → SetBaseFee → SetParams`, the Cosmos-tx ante handler permanently uses the genesis-time (or app-startup-time) base fee and `MinGasPrice` for all subsequent blocks. Any user submitting a Cosmos-native transaction after the base fee has risen can commit that transaction while paying fees calculated against the stale, lower base fee.

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` reads params once:

```go
evmParams := options.EvmKeeper.GetParams(ctx)
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

The pointer `&feemarketParams` escapes into the `NewDynamicFeeChecker` closure and into `NewMinGasPriceDecorator`:

```go
cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [2](#0-1) 

Inside `NewDynamicFeeChecker`, every invocation reads the base fee from the captured stale struct:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [3](#0-2) 

`types.GetBaseFee` simply calls `feemarketParams.GetBaseFee()` on the captured snapshot — it never queries the live KV store:

```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()
``` [4](#0-3) 

Meanwhile, the live base fee is updated every block in `feemarket/keeper/abci.go`:

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [5](#0-4) 

`SetBaseFee` writes the new value into the persistent KV store via `SetParams`:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [6](#0-5) 

The Ethereum ante handler (`newEthAnteHandler`) correctly avoids this problem by calling `EVMBlockConfig` per-transaction, which reads fresh params from the store on every call:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
...
baseFee := blockCfg.BaseFee
``` [7](#0-6) 

`EVMBlockConfig` reads `feemarketParams` live from the store on first call per block:

```go
feemarketParams := k.feeMarketKeeper.GetParams(ctx)
...
baseFee = feemarketParams.GetBaseFee()
``` [8](#0-7) 

The same stale-snapshot problem applies to `MinGasPriceDecorator`, which reads `mpd.feemarketParams.MinGasPrice` from the captured struct rather than the live store:

```go
minGasPrice := mpd.feemarketParams.MinGasPrice
``` [9](#0-8) 

### Impact Explanation

When `DynamicFeeChecker` is enabled, every Cosmos-native transaction processed after the base fee has risen from its startup value passes the `feeCap.LT(baseFeeInt)` guard using the stale lower base fee. The `effectiveFee` returned by the checker — and subsequently deducted by `DeductFeeDecorator` — is computed as `min(stale_baseFee + tip, feeCap) * gas`, which is less than the correct `min(current_baseFee + tip, feeCap) * gas`. The transaction commits with fees below the current protocol minimum, causing persistent fee mis-accounting: the fee collector (and validators) receive less than the EIP-1559 fee market requires. Additionally, if governance raises `MinGasPrice` to combat spam, the stale `MinGasPriceDecorator` never enforces the new floor, allowing Cosmos transactions to bypass the updated spam protection entirely.

This matches the allowed High impact: *"ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

The base fee changes every block. From the very first block after app startup, the Cosmos ante handler's snapshot is stale. No special privileges are required — any user submitting a Cosmos-native transaction (e.g., `MsgSend`, IBC, governance) while `DynamicFeeChecker = true` can underpay fees. The gap between stale and live base fee grows monotonically during periods of sustained high gas usage, making the underpayment increasingly significant over time.

### Recommendation

Replace the value-copy snapshot pattern with a live-store read inside the closure. Both `NewDynamicFeeChecker` and `NewMinGasPriceDecorator` should accept the keeper interface and query params on every invocation, mirroring the pattern already used by `newEthAnteHandler`:

```go
// Instead of capturing &feemarketParams at construction time:
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams) // stale

// Read live params inside the closure:
liveParams := feeMarketKeeper.GetParams(ctx)
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &liveParams)    // fresh
```

Apply the same fix to `MinGasPriceDecorator.AnteHandle` so it calls `mpd.feesKeeper.GetParams(ctx).MinGasPrice` rather than reading from the cached struct field.

### Proof of Concept

1. Chain starts with `BaseFee = 1_000_000_000` (1 Gwei). `newCosmosAnteHandler` is called; `feemarketParams.BaseFee = 1_000_000_000` is captured.
2. Blocks run at above-target gas usage. After N blocks, `BeginBlock` has updated the live store base fee to `5_000_000_000` (5 Gwei).
3. Attacker submits a Cosmos `MsgSend` with `fee = 1_000_000_000 * gasLimit` (1 Gwei price).
4. `NewDynamicFeeChecker` evaluates `feeCap = 1_000_000_000`, checks `feeCap.LT(stale_baseFeeInt = 1_000_000_000)` → false → passes.
5. `effectiveFee = 1_000_000_000 * gasLimit` is deducted — 5× less than the current protocol requires.
6. Transaction commits. The fee collector receives 1 Gwei/gas instead of the correct 5 Gwei/gas.

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

**File:** evmd/ante/handler_options.go (L179-188)
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
	}
```

**File:** evmd/ante/handler_options.go (L198-199)
```go
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		ante.NewValidateMemoDecorator(options.AccountKeeper),
```

**File:** ante/evm/fee_checker.go (L56-60)
```go
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

**File:** x/feemarket/keeper/abci.go (L30-38)
```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
	baseFee := k.CalculateBaseFee(ctx)

	// return immediately if base fee is nil
	if baseFee == nil {
		return nil
	}

	k.SetBaseFee(ctx, baseFee)
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

**File:** x/evm/keeper/config.go (L85-100)
```go
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

**File:** ante/cosmos/min_gas_price.go (L54-54)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice
```
