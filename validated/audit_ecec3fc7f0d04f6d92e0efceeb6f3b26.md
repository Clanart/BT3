### Title
Stale `feemarketParams.BaseFee` Captured at Construction Time in `NewDynamicFeeChecker` Allows Cosmos Txs to Bypass Current EIP-1559 Base Fee - (File: `ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` captures `feemarketParams` by pointer at construction time inside `newCosmosAnteHandler`. Because `feemarketParams` is a local variable whose value is fixed when `newCosmosAnteHandler` is called, the base fee used to validate Cosmos SDK transaction fees never updates — even though the EIP-1559 base fee is recalculated and stored every block in `BeginBlock`. This is the direct analog of the Derby `exchangeRateStored` bug: a cached/stale value is used for accounting instead of the live on-chain value.

---

### Finding Description

**Root cause — `newCosmosAnteHandler` captures a snapshot of `feemarketParams` at construction time:**

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` fetches `feemarketParams` once from the keeper and immediately passes its address to `NewDynamicFeeChecker`:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, ...) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // snapshot
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)  // pointer to local
``` [1](#0-0) 

`NewDynamicFeeChecker` returns a closure that captures the pointer and reads `feemarketParams.BaseFee` on every call:

```go
func NewDynamicFeeChecker(..., feemarketParams *feemarkettypes.Params) authante.TxFeeChecker {
    return func(ctx sdk.Context, tx sdk.Tx) (sdk.Coins, int64, error) {
        ...
        baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads directly from the captured `feemarketParams` struct — not from the KVStore:

```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()   // reads captured snapshot
    return baseFee
}
``` [3](#0-2) 

**The base fee changes every block but the snapshot never updates:**

`BeginBlock` recalculates and stores a new base fee into the KVStore on every block:

```go
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [4](#0-3) 

`SetBaseFee` writes the updated value into `params.BaseFee` in the persistent KVStore:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [5](#0-4) 

But the `feemarketParams` local variable captured in the `NewDynamicFeeChecker` closure is never refreshed. It holds the value from the moment `newCosmosAnteHandler` was called (app startup / ante handler construction), not the live KVStore value.

**Contrast with the EVM ante handler, which always reads fresh params:**

`newEthAnteHandler` calls `EVMBlockConfig` per transaction, which reads `feemarketParams` fresh from the KVStore (cached once per block in the object store):

```go
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        baseFee := blockCfg.BaseFee
``` [6](#0-5) 

```go
func (k *Keeper) EVMBlockConfig(ctx sdk.Context, chainID *big.Int) (*EVMBlockConfig, error) {
    ...
    feemarketParams := k.feeMarketKeeper.GetParams(ctx)   // live KVStore read
    ...
    baseFee = feemarketParams.GetBaseFee()
``` [7](#0-6) 

EVM transactions always use the current block's base fee. Cosmos SDK transactions using `NewDynamicFeeChecker` always use the stale snapshot.

---

### Impact Explanation

The stale base fee is used in two places inside `NewDynamicFeeChecker`:

1. **Fee cap check** — `feeCap.LT(baseFeeInt)` rejects txs whose fee-per-gas is below the base fee. With a stale (lower) base fee, Cosmos SDK transactions whose fee-per-gas is above the stale base fee but below the current base fee are accepted when they should be rejected. [8](#0-7) 

2. **Effective fee deduction** — `effectivePrice` and `effectiveFee` are computed from the stale base fee, so the amount actually deducted from the sender is lower than it should be. [9](#0-8) 

Over many blocks of sustained high gas usage, the EIP-1559 base fee can grow by orders of magnitude from its initial value. A Cosmos SDK transaction paying only the initial base fee (e.g., 1 Gwei) would pass the ante handler even when the live base fee is 100 Gwei. This is a direct fee mis-accounting bug: invalid transactions (insufficient fee) commit, and user funds/fees are mis-accounted relative to the protocol's current fee market state.

This maps to: **High — fee market ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted.**

---

### Likelihood Explanation

The base fee changes on every block where gas usage differs from the target (which is the common case on any active network). Any Cosmos SDK transaction submitted after even a single block of above-target gas usage will be validated against a stale base fee. No special privileges, keys, or network access are required — any unprivileged user can submit a Cosmos SDK transaction. The attack is passive: simply submit a Cosmos SDK tx with a fee above the stale base fee but below the current base fee.

---

### Recommendation

Inside `NewDynamicFeeChecker`, do not rely on the captured `feemarketParams` pointer for the base fee. Instead, read the live base fee from the keeper at transaction time, analogous to how `EVMBlockConfig` does it:

```go
// Instead of:
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)

// Use a live read, e.g. pass the feemarket keeper and call:
baseFee := feemarketKeeper.GetBaseFee(ctx)
```

Alternatively, if passing the keeper is undesirable, re-fetch `feemarketParams` from the keeper inside the closure on each invocation rather than capturing a snapshot at construction time.

---

### Proof of Concept

1. Chain starts; initial base fee = 1 Gwei. `newCosmosAnteHandler` is called; `feemarketParams.BaseFee = 1 Gwei` is captured.
2. Network sustains high gas usage for N blocks. `BeginBlock` updates the KVStore base fee to 100 Gwei via `CalculateBaseFee` → `SetBaseFee`.
3. Attacker submits a Cosmos SDK transaction (e.g., `MsgSend`) with `fee = 2 Gwei * gasLimit` and `ExtensionOptionDynamicFeeTx`.
4. `NewDynamicFeeChecker` closure executes: `baseFee = feemarketParams.GetBaseFee()` → returns stale 1 Gwei.
5. Check `feeCap (2 Gwei) >= baseFee (1 Gwei)` → passes. Effective fee deducted = `min(1+tip, 2) * gas` ≈ 2 Gwei × gas.
6. The transaction commits. The correct rejection threshold was 100 Gwei × gas; the attacker paid ~2% of the required fee.
7. An EVM transaction with the same fee would have been correctly rejected by `newEthAnteHandler` (which reads the live 100 Gwei base fee via `EVMBlockConfig`).

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

**File:** ante/evm/fee_checker.go (L83-88)
```go
		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

		if feeCap.LT(baseFeeInt) {
			return nil, 0, errorsmod.Wrapf(errortypes.ErrInsufficientFee, "insufficient gas prices; got: %s required: %s", feeCap, baseFeeInt)
		}
```

**File:** ante/evm/fee_checker.go (L91-98)
```go
		effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))

		// NOTE: create a new coins slice without having to validate the denom
		effectiveFee := sdk.Coins{
			{
				Denom:  denom,
				Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),
			},
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

**File:** x/evm/keeper/config.go (L82-100)
```go
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
