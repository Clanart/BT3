### Title
Integer Truncation in `NewDynamicFeeChecker` Produces Zero Effective Fee for Cosmos Transactions When Base Fee Is Zero - (File: `ante/evm/fee_checker.go`)

### Summary
`NewDynamicFeeChecker` computes the per-gas fee cap via integer (truncating) division: `feeCap = fee / gas`. When the total fee submitted is smaller than the gas limit (i.e., `fee < gas`), the truncation yields `feeCap = 0`. If the chain's base fee is also zero — which occurs whenever `NoBaseFee = true` is set in the feemarket params — the subsequent fee-sufficiency check passes (`0 < 0` is false), and the computed `effectiveFee` collapses to zero. The Cosmos transaction is therefore admitted and committed with no fee charged, regardless of the non-zero fee the sender attached.

### Finding Description
In `ante/evm/fee_checker.go`, `NewDynamicFeeChecker` computes:

```go
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))   // integer truncation
baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)

if feeCap.LT(baseFeeInt) {                          // 0 < 0 → false, passes
    return nil, 0, errorsmod.Wrapf(...)
}

effectivePrice := sdkmath.NewIntFromBigInt(
    types.EffectiveGasPrice(baseFeeInt.BigInt(), feeCap.BigInt(), maxPriorityPrice.BigInt()),
)
// = min(0 + maxPriorityPrice, 0) = 0

effectiveFee := sdk.Coins{{
    Denom:  denom,
    Amount: effectivePrice.Mul(sdkmath.NewIntFromUint64(gas)),  // 0 * gas = 0
}}
``` [1](#0-0) 

When `baseFee = 0` (set by `NoBaseFee = true` in feemarket params, which returns `big.NewInt(0)` rather than `nil` and therefore does **not** trigger the `checkTxFeeWithValidatorMinGasPrices` fallback at line 57–60), and the sender submits any `fee < gas`, the truncation produces `feeCap = 0`, `effectivePrice = 0`, and `effectiveFee = sdk.Coins{}` (zero). The `TxFeeChecker` returns this zero-coin slice to the SDK ante handler, which deducts nothing from the sender. [2](#0-1) 

The feemarket `NoBaseFee` parameter is explicitly designed to force the base fee to zero for zero-price calls, and its default value is `false` — but it is a governance-mutable parameter. The default `MinGasPrice` is also `0`, meaning the `MinGasPriceDecorator` provides no backstop in the default configuration. [3](#0-2) 

### Impact Explanation
Any unprivileged sender can submit a Cosmos SDK transaction (e.g., `MsgEthereumTx` wrapped in a Cosmos tx, governance votes, IBC messages, staking operations) with `fee = 1` (one unit of the EVM denom) and any `gas` value ≥ 2. When `NoBaseFee = true` and `MinGasPrice = 0`, the ante handler charges zero fee and the transaction is committed. This constitutes a fee market ante handler bug that permits transactions with effectively zero fee to commit, mis-accounting fees owed to the fee collector module. An attacker can flood the mempool and block space with negligible cost, degrading liveness for legitimate users. This maps to the allowed High impact: *"ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation
`NoBaseFee = true` is a governance-settable parameter used in production deployments that want fixed or zero gas prices (e.g., permissioned chains, testnets, chains that have not yet enabled EIP-1559). The default `MinGasPrice = 0` provides no compensating control. Any chain operator or governance proposal that sets `NoBaseFee = true` immediately opens this path to every unprivileged user who can craft a Cosmos tx with `fee < gas`.

### Recommendation
Replace the truncating integer division with a check that ensures the submitted fee is at least `baseFee * gas` before computing `feeCap`, or add a minimum effective-fee guard after computing `effectiveFee`:

```go
// After computing effectiveFee, reject if it is zero but the sender
// provided a non-zero fee (dust guard analogous to the external report).
if effectiveFee.IsZero() && !feeCoins.IsZero() {
    return nil, 0, errorsmod.Wrapf(
        errortypes.ErrInsufficientFee,
        "fee too small to cover gas: provided %s for %d gas units",
        feeCoins, gas,
    )
}
```

Alternatively, use ceiling division (`fee.Add(gas-1).Quo(gas)`) when computing `feeCap` so that any non-zero fee always produces a non-zero per-gas price, preventing the silent collapse to zero.

### Proof of Concept
1. Deploy an Ethermint chain with `NoBaseFee = true` and `MinGasPrice = 0` (both achievable via governance or genesis).
2. Construct a Cosmos SDK tx (e.g., a `MsgEthereumTx`) with `gas = 21000` and `fee = [{"denom":"aphoton","amount":"1"}]` (1 unit, far below `gas`).
3. Submit via `BroadcastTx`. The `NewDynamicFeeChecker` computes `feeCap = 1 / 21000 = 0`, `effectivePrice = min(0 + MaxInt64, 0) = 0`, `effectiveFee = 0`.
4. The ante handler deducts zero from the sender's balance; the tx is included in a block.
5. Repeat indefinitely — each tx costs the sender only the 1-unit fee that is never actually deducted, enabling free-of-charge block space consumption. [4](#0-3)

### Citations

**File:** ante/evm/fee_checker.go (L56-60)
```go
		baseFee := types.GetBaseFee(ctx.BlockHeight(), ethCfg, feemarketParams)
		if baseFee == nil {
			// london hardfork is not enabled: fallback to min-gas-prices logic
			return checkTxFeeWithValidatorMinGasPrices(ctx, feeTx)
		}
```

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

**File:** x/feemarket/types/params.go (L27-35)
```go
var (
	// DefaultMinGasMultiplier is 0.5 or 50%
	DefaultMinGasMultiplier = sdkmath.LegacyNewDecWithPrec(50, 2)
	// DefaultMinGasPrice is 0 (i.e disabled)
	DefaultMinGasPrice = sdkmath.LegacyZeroDec()
	// DefaultEnableHeight is 0 (i.e disabled)
	DefaultEnableHeight = int64(0)
	// DefaultNoBaseFee is false
	DefaultNoBaseFee = false
```
