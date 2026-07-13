### Title
Stale Params Snapshot in `NewDynamicFeeChecker` Allows Cosmos SDK Transactions to Bypass Current EIP-1559 Base Fee - (File: `ante/evm/fee_checker.go`, `evmd/ante/handler_options.go`)

### Summary

`NewDynamicFeeChecker`, used for Cosmos SDK (non-EVM) transactions, captures `feemarketParams` as a one-time snapshot at ante handler construction time. Because the EIP-1559 base fee changes every block, the checker permanently uses a stale base fee for all subsequent Cosmos SDK transaction fee validation. An unprivileged user can submit Cosmos SDK transactions with fees based on the old (lower) base fee and have them accepted and committed even when the current base fee is higher, causing systematic fee mis-accounting.

### Finding Description

`newCosmosAnteHandler` (and `newLegacyCosmosAnteHandlerEip712`) reads `feemarketParams` once at construction time and passes a pointer to that local copy into `NewDynamicFeeChecker` and `MinGasPriceDecorator`:

```go
// evmd/ante/handler_options.go lines 179-187
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)          // snapshot, never refreshed
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)  // snapshot, never refreshed
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams)
```

The returned `sdk.AnteHandler` closure captures `&feemarketParams` — a pointer to a local variable whose value is fixed at construction time and never updated. Every subsequent call to the checker reads the stale snapshot:

```go
// ante/evm/fee_checker.go line 56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
```

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` — the frozen snapshot value — not the live store.

By contrast, `newEthAnteHandler` (for EVM transactions) correctly reads fresh params on every invocation:

```go
// evmd/ante/handler_options.go lines 88-95
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        feemarketParams := &blockCfg.FeeMarketParams
        baseFee := blockCfg.BaseFee   // live, per-invocation
```

The asymmetry is the root cause: EVM txs get the live base fee; Cosmos SDK txs get the genesis/startup base fee forever.

The `feeCap` check at line 86 and the `effectiveFee` computation at line 97 both derive from the stale `baseFee`:

```go
// ante/evm/fee_checker.go lines 83-97
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)   // stale

if feeCap.LT(baseFeeInt) {                         // checked against stale value
    return nil, 0, errorsmod.Wrapf(...)
}
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
effectiveFee := sdk.Coins{{
    Denom:  denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),  // deducted amount also stale
}}
```

`MinGasPriceDecorator` has the same defect — it reads `mpd.feemarketParams.MinGasPrice` from the snapshot rather than querying the live keeper (which it holds but never uses for this purpose):

```go
// ante/cosmos/min_gas_price.go line 54
minGasPrice := mpd.feemarketParams.MinGasPrice   // stale snapshot
```

### Impact Explanation

The EIP-1559 base fee adjusts every block via `CalculateBaseFee` in `BeginBlock` and is written back to the persistent store. After the first block where the base fee rises above its genesis value, every Cosmos SDK transaction validated by `NewDynamicFeeChecker` is checked against the wrong (lower) base fee. The user pays `oldBaseFee * gas` instead of `currentBaseFee * gas`. The fee deducted from the sender's balance is also computed from the stale value, so the under-payment is committed to state. This is a fee market ante handler bug that permits invalid transactions (those with fees below the current base fee) to commit, and causes valid user funds/fees to be mis-accounted.

### Likelihood Explanation

The base fee changes every block whenever gas usage deviates from the target. On any active chain, the base fee will diverge from its genesis value within the first few blocks. Any user submitting a Cosmos SDK transaction (governance votes, IBC relays, staking operations, etc.) with `DynamicFeeChecker` enabled will trigger this path. No special privileges, keys, or coordination are required — a standard unprivileged Cosmos SDK transaction is sufficient.

### Recommendation

`NewDynamicFeeChecker` must not capture params at construction time. It should accept a keeper interface and read the live base fee from the store on every invocation, matching the pattern used by `newEthAnteHandler`:

```go
func NewDynamicFeeChecker(fmKeeper FeeMarketKeeper, evmKeeper EVMKeeper) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        baseFee := fmKeeper.GetBaseFee(ctx)   // live read per invocation
        ...
    }
}
```

Similarly, `MinGasPriceDecorator.AnteHandle` should call `mpd.feesKeeper.GetParams(ctx).MinGasPrice` instead of reading from the frozen `mpd.feemarketParams` pointer.

### Proof of Concept

1. Chain starts with `BaseFee = 1_000_000_000` (genesis default).
2. Network activity causes several full blocks; `CalculateBaseFee` raises `BaseFee` to `2_000_000_000` after block N.
3. Attacker submits a Cosmos SDK transaction (e.g., `MsgVote`) with `fee = 1_000_000_000 * gasLimit` (the genesis base fee).
4. `newCosmosAnteHandler` was constructed at startup with the genesis snapshot; `feemarketParams.BaseFee = 1_000_000_000`.
5. `NewDynamicFeeChecker` computes `feeCap = fee / gas = 1_000_000_000`, compares against stale `baseFeeInt = 1_000_000_000` — check passes.
6. `effectiveFee = 1_000_000_000 * gas` is deducted and the transaction commits.
7. The correct fee at block N should have been `2_000_000_000 * gas`; the attacker paid half the required amount. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** ante/evm/fee_checker.go (L42-56)
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
```

**File:** ante/evm/fee_checker.go (L83-97)
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
```

**File:** ante/cosmos/min_gas_price.go (L44-54)
```go
func NewMinGasPriceDecorator(fk interfaces.FeeMarketKeeper, evmDenom string, feemarketParams *feemarkettypes.Params) MinGasPriceDecorator {
	return MinGasPriceDecorator{feesKeeper: fk, evmDenom: evmDenom, feemarketParams: feemarketParams}
}

func (mpd MinGasPriceDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (newCtx sdk.Context, err error) {
	feeTx, ok := tx.(sdk.FeeTx)
	if !ok {
		return ctx, errorsmod.Wrapf(errortypes.ErrInvalidType, "invalid transaction type %T, expected sdk.FeeTx", tx)
	}

	minGasPrice := mpd.feemarketParams.MinGasPrice
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

**File:** x/feemarket/keeper/eip1559.go (L32-46)
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
```
