### Title
Integer Truncation in `NewDynamicFeeChecker` Allows Zero-Fee Cosmos Transactions When BaseFee Is Zero — (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` computes the per-gas fee cap as `feeCap = fee / gas` using integer (floor) division. When `fee < gas`, `feeCap` truncates to `0`. If the chain's `baseFee` is also `0` — the default-reachable state when `MinGasPrice = 0` — the fee-sufficiency check passes, `effectivePrice` resolves to `0`, and the `DeductFeeDecorator` deducts nothing from the sender. Any unprivileged user can submit Cosmos transactions (governance, staking, IBC relay, etc.) at zero cost.

### Finding Description

**Step 1 — Truncation of feeCap**

In `ante/evm/fee_checker.go` line 83:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

`sdkmath.Int.Quo` is integer floor division. For any tx where `fee < gas` (e.g., `fee = 1 aevmos`, `gas = 2`), `feeCap = 0`. [1](#0-0) 

**Step 2 — Sufficiency check passes when baseFee = 0**

```go
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)
if feeCap.LT(baseFeeInt) { ... }   // 0 < 0 → false → passes
```

`DefaultMinGasPrice` is `sdkmath.LegacyZeroDec()`, so the base fee is allowed to decay to `0` under low gas usage. [2](#0-1) 

**Step 3 — effectivePrice collapses to zero**

```go
effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()))
// = min(maxPriorityPrice + 0, 0) = 0
effectiveFee := effectivePrice.Mul(sdkmath.NewIntFromUint64(gas))  // 0 * gas = 0
```

`EffectiveGasPrice` is `min(tipCap + baseFee, feeCap)` = `min(maxPriorityPrice, 0)` = `0`. [3](#0-2) [4](#0-3) 

**Step 4 — DeductFeeDecorator deducts nothing**

`DeductFeeDecorator.AnteHandle` overwrites `fee` with the checker's return value, then calls `checkDeductFee`. Inside `checkDeductFee`:

```go
if !fee.IsZero() {
    err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
```

When `fee = 0`, the branch is skipped entirely — no coins are deducted. [5](#0-4) [6](#0-5) 

**Step 5 — Production path confirmed**

`DynamicFeeChecker: true` is hardcoded in `evmd/app.go`, so `NewDynamicFeeChecker` is active for all Cosmos txs in production. `newCosmosAnteHandler` (called per-tx) constructs the checker with a fresh `feemarketParams` snapshot, so the `baseFee = 0` condition is evaluated against the live chain state. [7](#0-6) [8](#0-7) 

The `MinGasPriceDecorator` that precedes `DeductFeeDecorator` in the chain short-circuits immediately when `minGasPrice.IsZero()`, providing no additional protection. [9](#0-8) 

### Impact Explanation

When `baseFee = 0` (reachable by default since `DefaultMinGasPrice = 0`), any user can submit Cosmos transactions — including governance votes, staking messages, IBC relays, or authz executions — with a fee of `1 aevmos` and a gas limit of `2` (or any `fee < gas`). The ante handler accepts the transaction and deducts zero fees from the sender's account. This is a fee mis-accounting bug that permits economically invalid (zero-fee) transactions to commit to state, matching the allowed impact: *"ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

`DefaultMinGasPrice = sdkmath.LegacyZeroDec()` is the out-of-box default. On any chain where `MinGasPrice` is not explicitly raised above zero, the EIP-1559 base fee will decay toward `0` whenever blocks are under-full. This is a common operational state during low-activity periods. The attacker-controlled input (`fee < gas`) requires no special privilege — any account can craft such a Cosmos tx. The condition is therefore reachable by any unprivileged user on a default-configured Ethermint chain.

### Recommendation

Replace floor division with ceiling division when computing `feeCap`, so that a fee of `1` with gas `2` yields `feeCap = 1` rather than `0`:

```go
// ceil(fee / gas)
one := sdkmath.NewIntFromUint64(gas - 1)
feeCap := fee.Add(one).Quo(sdkmath.NewIntFromUint64(gas))
```

Alternatively, use `sdkmath.LegacyDec` arithmetic to preserve sub-integer precision before comparing against `baseFeeInt`, consistent with how `CheckEthMinGasPrice` handles the same computation for EVM txs.

### Proof of Concept

**Precondition**: Chain running with default params (`MinGasPrice = 0`, `NoBaseFee = false`), base fee has decayed to `0` due to low gas usage.

1. Attacker constructs a Cosmos tx (e.g., `MsgDelegate`) with `fee = 1 aevmos`, `gas = 2`.
2. `NewDynamicFeeChecker` executes:
   - `feeCap = 1 / 2 = 0` (integer truncation)
   - `baseFeeInt = 0`
   - `0 < 0` → false → fee check passes
   - `effectivePrice = min(MaxInt64 + 0, 0) = 0`
   - `effectiveFee = {0 aevmos}`
3. `DeductFeeDecorator` receives `fee = {0 aevmos}`, skips deduction (`!fee.IsZero()` is false).
4. Transaction commits. Attacker paid `0 aevmos` in fees. [10](#0-9)

### Citations

**File:** ante/evm/fee_checker.go (L79-99)
```go
		gas := feeTx.GetGas()
		feeCoins := feeTx.GetFee()
		fee := feeCoins.AmountOf(denom)

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

**File:** x/feemarket/types/params.go (L30-31)
```go
	// DefaultMinGasPrice is 0 (i.e disabled)
	DefaultMinGasPrice = sdkmath.LegacyZeroDec()
```

**File:** x/evm/types/utils.go (L230-234)
```go
// EffectiveGasPrice compute the effective gas price based on eip-1159 rules
// `effectiveGasPrice = min(baseFee + tipCap, feeCap)`
func EffectiveGasPrice(baseFee *big.Int, feeCap *big.Int, tipCap *big.Int) *big.Int {
	return ethermint.BigMin(new(big.Int).Add(tipCap, baseFee), feeCap)
}
```

**File:** ante/evm/nativefee.go (L57-65)
```go
	fee := feeTx.GetFee()
	if !simulate {
		fee, priority, err = dfd.txFeeChecker(ctx, tx)
		if err != nil {
			return ctx, err
		}
	}
	if err := dfd.checkDeductFee(ctx, tx, fee); err != nil {
		return ctx, err
```

**File:** ante/evm/nativefee.go (L109-115)
```go
	// deduct the fees
	if !fee.IsZero() {
		err := evmkeeper.DeductFees(dfd.bankKeeper, ctx, deductFeesFromAcc, fee)
		if err != nil {
			return err
		}
	}
```

**File:** evmd/app.go (L793-795)
```go
		ExtensionOptionChecker: ethermint.HasDynamicFeeExtensionOption,
		DynamicFeeChecker:      true,
		DisabledAuthzMsgs: []string{
```

**File:** evmd/ante/handler_options.go (L178-201)
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
		ante.NewValidateMemoDecorator(options.AccountKeeper),
		ante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		ante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
```

**File:** ante/cosmos/min_gas_price.go (L54-59)
```go
	minGasPrice := mpd.feemarketParams.MinGasPrice

	// Short-circuit if min gas price is 0 or if simulating
	if minGasPrice.IsZero() || simulate {
		return next(ctx, tx, simulate)
	}
```
