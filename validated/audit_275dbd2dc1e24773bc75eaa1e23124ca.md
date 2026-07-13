### Title
Stale `feemarketParams` Snapshot in `NewDynamicFeeChecker` Allows Cosmos Transactions to Bypass the Live EIP-1559 Base Fee — (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` captures a pointer to a one-time snapshot of `feemarketParams` taken at ante handler construction (app startup). Because `feemarketParams.BaseFee` is never refreshed from the KV store, every Cosmos SDK transaction validated by this checker is checked against the genesis-era base fee, not the live one. As the EIP-1559 base fee rises with chain usage, any unprivileged user can submit Cosmos transactions with fees calculated at the stale (lower) base fee and have them accepted and committed, systematically under-paying fees.

### Finding Description

In `newCosmosAnteHandler` (and `newLegacyCosmosAnteHandlerEip712`), fee market params are read once from the store at construction time and their address is captured:

```go
feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [1](#0-0) 

`NewDynamicFeeChecker` stores this pointer and uses it on every subsequent transaction:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`feemarketParams` is a Go heap-allocated copy of the params struct at construction time. `SetBaseFee` → `SetParams` updates the KV store each `BeginBlock`:

```go
k.SetBaseFee(ctx, baseFee)
``` [3](#0-2) 

But the captured pointer is never refreshed. `feemarketParams.BaseFee` permanently holds the genesis value.

By contrast, the **Ethereum** ante handler path reads fresh params on every transaction:

```go
blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
feemarketParams := &blockCfg.FeeMarketParams
baseFee := blockCfg.BaseFee
``` [4](#0-3) 

The Cosmos path has no equivalent live read, creating an asymmetry: EVM transactions are checked against the live base fee; Cosmos transactions are checked against the genesis base fee.

The fee check inside the checker computes:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)
if feeCap.LT(baseFeeInt) {
    return nil, 0, errorsmod.Wrapf(...)
}
``` [5](#0-4) 

With a stale `baseFee` equal to the genesis value, this check passes for any fee ≥ genesis base fee, regardless of how high the live base fee has risen.

### Impact Explanation

Cosmos SDK transactions (non-EVM) that use the dynamic fee checker pay fees based on the genesis base fee. The EIP-1559 base fee can increase up to 12.5% per block under sustained load:

```go
baseFeeDelta := ethermint.BigMax(x.Div(y, baseFeeChangeDenominator), common.Big1)
return x.Add(parentBaseFee, baseFeeDelta)
``` [6](#0-5) 

After sustained usage, the live base fee can be orders of magnitude above genesis. Cosmos transactions accepted at the stale base fee commit with severely under-paid fees. This is a fee market ante handler bug that permits invalid transactions (insufficient fees) to commit and mis-accounts EVM-denom fees collected by the fee collector module.

This matches the allowed High impact: *"ante handler … bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

No special privileges are required. Any user can submit a Cosmos SDK transaction on a chain with `DynamicFeeChecker` enabled. The base fee rises naturally with chain usage. The gap between the stale genesis base fee and the live base fee grows monotonically over time, making the bypass increasingly severe and trivially exploitable on any active chain.

### Recommendation

Read fee market params fresh from the KV store on each transaction invocation inside `NewDynamicFeeChecker`, rather than capturing a stale pointer at construction time. Either:

1. Pass the `FeeMarketKeeper` into the checker and call `keeper.GetParams(ctx)` per invocation (matching the pattern used in `newEthAnteHandler`), or
2. Restructure `newCosmosAnteHandler` to reconstruct the fee checker per block using a fresh context, consistent with how `EVMBlockConfig` is used in the Ethereum path.

### Proof of Concept

1. Chain starts; `newCosmosAnteHandler` is called once with genesis context. `feemarketParams.BaseFee = 1_000_000_000` (1 Gwei) is captured.
2. Over N blocks of high usage, `BeginBlock` → `CalculateBaseFee` → `SetBaseFee` raises the live base fee to `10_000_000_000` (10 Gwei) in the KV store. [7](#0-6) 
3. Attacker submits a Cosmos SDK transaction with `fee = 1_000_000_000 * gasLimit` (genesis base fee).
4. `NewDynamicFeeChecker` evaluates `feeCap >= feemarketParams.BaseFee = 1_000_000_000` → **passes**, because the stale snapshot is used. [8](#0-7) 
5. The transaction is accepted and committed despite paying 10× less than the live base fee of `10_000_000_000`.
6. The fee collector receives 10× less than required; the fee market's gas accounting is distorted for subsequent base fee calculations.

### Citations

**File:** evmd/ante/handler_options.go (L88-96)
```go
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

**File:** ante/evm/fee_checker.go (L83-99)
```go
		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}

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

**File:** x/feemarket/keeper/eip1559.go (L80-91)
```go
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
```
