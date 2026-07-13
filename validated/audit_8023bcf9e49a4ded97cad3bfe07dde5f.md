### Title
Stale `feemarketParams` Captured at Construction Time in `newCosmosAnteHandler` Causes Fee Mis-Accounting for Cosmos Transactions - (`evmd/ante/handler_options.go`)

### Summary

`newCosmosAnteHandler` reads `feemarketParams` (including `BaseFee` and `MinGasPrice`) once at construction time and passes a pointer to that snapshot into both `NewDynamicFeeChecker` and `MinGasPriceDecorator`. Because `BaseFee` is updated every block by the EIP-1559 adjustment logic, the Cosmos ante handler permanently operates on a stale base fee, while the EVM ante handler correctly reads fresh params on every invocation. This is the direct analog of the ERC-4626 `maxDeposit`/`maxWithdraw` bug: a "limit" value is read once and cached, while the actual enforcement path uses a different (live) value, causing the two to diverge.

---

### Finding Description

In `evmd/ante/handler_options.go`, `newCosmosAnteHandler` captures `feemarketParams` from the keeper at construction time:

```go
func newCosmosAnteHandler(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
    evmParams := options.EvmKeeper.GetParams(ctx)
    feemarketParams := options.FeeMarketKeeper.GetParams(ctx)   // ← snapshot, never refreshed
    ...
    txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
    ...
    cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams)
``` [1](#0-0) 

The `NewDynamicFeeChecker` closure then uses this stale snapshot to derive the base fee:

```go
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
``` [2](#0-1) 

`types.GetBaseFee` reads `feemarketParams.GetBaseFee()` — the value frozen at construction, not the live store value:

```go
func GetBaseFee(height int64, ethCfg *params.ChainConfig, feemarketParams *feemarkettypes.Params) *big.Int {
    ...
    baseFee := feemarketParams.GetBaseFee()
``` [3](#0-2) 

`MinGasPriceDecorator` similarly reads the stale snapshot:

```go
minGasPrice := mpd.feemarketParams.MinGasPrice
``` [4](#0-3) 

**Contrast with the EVM ante handler**, which reads fresh params on every invocation:

```go
func newEthAnteHandler(options HandlerOptions) sdk.AnteHandler {
    return func(ctx sdk.Context, tx sdk.Tx, simulate bool) (sdk.Context, error) {
        blockCfg, err := options.EvmKeeper.EVMBlockConfig(ctx, options.EvmKeeper.ChainID())
        ...
        feemarketParams := &blockCfg.FeeMarketParams
        baseFee := blockCfg.BaseFee
``` [5](#0-4) 

The live `BaseFee` is updated every block by `CalculateBaseFee` / `SetBaseFee`:

```go
func (k Keeper) SetBaseFee(ctx sdk.Context, baseFee *big.Int) {
    params := k.GetParams(ctx)
    params.BaseFee = ethermint.SaturatedNewInt(baseFee)
    err := k.SetParams(ctx, params)
``` [6](#0-5) 

The `NewDynamicFeeChecker` computes the effective fee to deduct as:

```go
effectivePrice := sdkmath.NewIntFromBigInt(types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
effectiveFee := sdk.Coins{{ Denom: denom, Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)) }}
``` [7](#0-6) 

Because `baseFeeInt` is derived from the stale snapshot, the `effectiveFee` deducted from the user is computed against the wrong base fee.

---

### Impact Explanation

**Scenario A — Base fee has risen since construction (common under sustained load):**

- Stale `BaseFee` = B₀ (genesis or last restart value)
- Current `BaseFee` = B₁ > B₀
- A Cosmos tx with `feeCap` ∈ `[B₀, B₁)` passes `NewDynamicFeeChecker` (stale check: `feeCap >= B₀` ✓) and has `effectiveFee = B₀ * gas` deducted
- The same tx would be rejected by the EVM ante handler (which uses live B₁)
- Result: **invalid Cosmos txs commit with under-paid fees** — fee mis-accounting, validators/stakers receive less than the protocol requires

**Scenario B — Base fee has fallen since construction:**

- Stale `BaseFee` = B₀ > current `BaseFee` = B₁
- A Cosmos tx with `feeCap` ∈ `[B₁, B₀)` is rejected by `NewDynamicFeeChecker` even though it satisfies the live base fee
- Result: **valid Cosmos txs are incorrectly rejected**

Both scenarios match the allowed High impact: *"fee market, ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

---

### Likelihood Explanation

- `BaseFee` changes every block under EIP-1559. Any node that has been running for more than one block after construction of the Cosmos ante handler will have a stale base fee.
- Any unprivileged user submitting a Cosmos SDK transaction (e.g., `MsgSend`, IBC relay, governance vote) with `DynamicFeeChecker` enabled is affected.
- No special privileges, governance access, or validator compromise required — a normal user submitting a Cosmos tx triggers the path.
- The divergence grows monotonically with uptime and network load, making exploitation increasingly easy over time.

---

### Recommendation

`NewDynamicFeeChecker` and `MinGasPriceDecorator` must read `feemarketParams` from the keeper on every invocation, not from a construction-time snapshot. The fix mirrors what `newEthAnteHandler` already does correctly:

```go
// In NewDynamicFeeChecker closure:
feemarketParams := feemarketKeeper.GetParams(ctx)   // read live params
baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, &feemarketParams)
```

```go
// In MinGasPriceDecorator.AnteHandle:
minGasPrice := mpd.feesKeeper.GetParams(ctx).MinGasPrice   // use keeper, not cached field
```

The `MinGasPriceDecorator` already holds `feesKeeper interfaces.FeeMarketKeeper` — it just needs to call `GetParams(ctx)` instead of reading the stale `mpd.feemarketParams`. [8](#0-7) 

---

### Proof of Concept

1. Deploy a chain with initial `BaseFee = 1_000_000_000` (1 Gwei). The Cosmos ante handler is constructed, capturing this value.
2. Submit many EVM transactions to fill blocks above the gas target. After N blocks, `BaseFee` rises to `5_000_000_000` (5 Gwei) in the live store.
3. Submit a Cosmos `MsgSend` with `fee = 2_000_000_000 * gasLimit` (2 Gwei effective feeCap).
4. **EVM ante handler** (if this were an EVM tx) would reject: `feeCap (2 Gwei) < current baseFee (5 Gwei)`.
5. **Cosmos ante handler** accepts: `feeCap (2 Gwei) >= stale baseFee (1 Gwei)` ✓. Deducts `1 Gwei * gasLimit` instead of `5 Gwei * gasLimit`.
6. The tx commits with 5× under-paid fees. Validators receive 80% less fee revenue than the protocol mandates for this block's base fee.

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

**File:** ante/evm/fee_checker.go (L91-99)
```go
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

**File:** ante/cosmos/min_gas_price.go (L36-46)
```go
type MinGasPriceDecorator struct {
	feesKeeper      interfaces.FeeMarketKeeper
	evmDenom        string
	feemarketParams *feemarkettypes.Params
}

// NewMinGasPriceDecorator creates a new MinGasPriceDecorator instance used only for
// Cosmos transactions.
func NewMinGasPriceDecorator(fk interfaces.FeeMarketKeeper, evmDenom string, feemarketParams *feemarkettypes.Params) MinGasPriceDecorator {
	return MinGasPriceDecorator{feesKeeper: fk, evmDenom: evmDenom, feemarketParams: feemarketParams}
}
```

**File:** ante/cosmos/min_gas_price.go (L54-54)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice
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
