### Title
Stale `feemarketParams` Captured at Construction Time Bypasses EIP-1559 Base Fee Enforcement for Cosmos Transactions — (File: `evmd/ante/handler_options.go`)

---

### Summary

`newCosmosAnteHandler` (and `newLegacyCosmosAnteHandlerEip712`) snapshot `feemarketParams` — including the live `BaseFee` field — **once at node startup**. Every Cosmos-SDK transaction processed thereafter is fee-validated against that frozen snapshot, not the current on-chain base fee. Because the EIP-1559 base fee is recalculated and written to state every block, any block where the base fee has risen above its startup value allows an unprivileged sender to include Cosmos transactions while paying fees below the protocol-mandated minimum.

---

### Finding Description

`newCosmosAnteHandler` is called once during app initialization to produce the `sdk.AnteHandler` that is reused for the lifetime of the node:

```go
// evmd/ante/handler_options.go  lines 178-211
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
    evmParams        := options.EvmKeeper.GetParams(ctx)          // snapshot at startup
    feemarketParams  := options.FeeMarketKeeper.GetParams(ctx)    // snapshot at startup
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
``` [1](#0-0) 

Both `NewDynamicFeeChecker` and `MinGasPriceDecorator` receive a **pointer to the local `feemarketParams` variable**. That variable is never mutated after construction, so every subsequent call reads the genesis/startup value of `BaseFee` and `MinGasPrice`.

`NewDynamicFeeChecker` derives the required base fee directly from the captured snapshot:

```go
// ante/evm/fee_checker.go  line 56
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`MinGasPriceDecorator` reads `MinGasPrice` from the same frozen struct:

```go
// ante/cosmos/min_gas_price.go  line 54
minGasPrice := mpd.feemarketParams.MinGasPrice
``` [3](#0-2) 

The base fee is updated every block in `BeginBlock` by writing a new value into `params.BaseFee` in the KV store:

```go
// x/feemarket/keeper/abci.go  lines 30-38
func (k *Keeper) BeginBlock(ctx sdk.Context) error {
    baseFee := k.CalculateBaseFee(ctx)
    ...
    k.SetBaseFee(ctx, baseFee)
``` [4](#0-3) 

`SetBaseFee` writes the new value back into `params.BaseFee` in the persistent store:

```go
// x/feemarket/keeper/params.go  lines 72-78
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [5](#0-4) 

The in-memory `feemarketParams` held by the Cosmos ante handler is **never refreshed** from the store, so it permanently diverges from the on-chain value.

By contrast, `newEthAnteHandler` correctly fetches a fresh `EVMBlockConfig` (which includes current `FeeMarketParams` and `BaseFee`) on every transaction:

```go
// evmd/ante/handler_options.go  lines 86-95
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        feemarketParams := &blockCfg.FeeMarketParams
        baseFee := blockCfg.BaseFee
``` [6](#0-5) 

The same stale-snapshot pattern is present in `newLegacyCosmosAnteHandlerEip712`:

```go
// evmd/ante/evm_handler.go  lines 29-37
evmParams        := options.EvmKeeper.GetParams(ctx)
feemarketParams  := options.FeeMarketKeeper.GetParams(ctx)
...
txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
``` [7](#0-6) 

---

### Impact Explanation

Any Cosmos-SDK transaction (governance votes, IBC relays, staking messages, authz executions, etc.) routed through `newCosmosAnteHandler` is fee-checked against the startup base fee. If the network has been running long enough for the EIP-1559 base fee to rise above its genesis value, an attacker can submit Cosmos transactions paying only the genesis-era fee. The fee collector module receives less than the protocol-mandated amount, mis-accounting fees for every such transaction. Because `DeliverTx` uses the same ante handler as `CheckTx`, these under-priced transactions are committed to state, not merely admitted to the mempool.

This matches the allowed High impact: **"EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."**

---

### Likelihood Explanation

The base fee changes every block on any active network. After even a modest period of above-target gas usage the live base fee will exceed the genesis value. Any user who inspects the startup `feemarketParams` (readable from the genesis file or the initial state) can craft Cosmos transactions with fees calibrated to the stale value and have them accepted indefinitely. No special privileges, keys, or coordination are required.

---

### Recommendation

Remove the one-time parameter snapshot from `newCosmosAnteHandler` and `newLegacyCosmosAnteHandlerEip712`. Instead, fetch `feemarketParams` (and `evmParams`) fresh from the keeper inside the returned closure on every invocation, mirroring the pattern already used in `newEthAnteHandler`:

```go
func newCosmosAnteHandler(options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        evmParams       := options.EvmKeeper.GetParams(ctx)
        feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
        // build decorators using fresh params ...
    }
}
```

Alternatively, pass the keeper interfaces into `NewDynamicFeeChecker` and `MinGasPriceDecorator` so they can query the store on each call, rather than holding a pointer to a frozen struct.

---

### Proof of Concept

1. Node starts with genesis `BaseFee = 1_000_000_000` (1 gwei). `newCosmosAnteHandler` captures this value.
2. Network runs with above-target gas usage for N blocks; `BeginBlock` raises the on-chain `BaseFee` to, say, `2_000_000_000` (2 gwei).
3. Attacker submits a Cosmos `MsgSend` with `fee = 1_000_000_000 * gasLimit` (1 gwei effective price).
4. `NewDynamicFeeChecker` evaluates `feeCap >= baseFee` using the stale `baseFee = 1_000_000_000` → check passes.
5. `MinGasPriceDecorator` evaluates against the stale `MinGasPrice` → check passes.
6. Transaction is included in the block paying half the required fee; the fee collector is under-credited by `1_000_000_000 * gasLimit` tokens per such transaction.

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

**File:** evmd/ante/handler_options.go (L178-198)
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
```

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

**File:** ante/cosmos/min_gas_price.go (L54-58)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
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
