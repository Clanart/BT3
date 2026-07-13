### Title
Division-by-Zero Panic in `NewDynamicFeeChecker` When Cosmos Tx `gas = 0` Halts the Chain - (`ante/evm/fee_checker.go`)

---

### Summary

`NewDynamicFeeChecker` and `getTxPriority` in `ante/evm/fee_checker.go` perform integer division using the transaction's gas limit as the divisor without any zero-guard. A Cosmos SDK transaction with `gas = 0` reaches these divisions, causing a Go runtime panic that halts the chain.

---

### Finding Description

In `ante/evm/fee_checker.go`, the `NewDynamicFeeChecker` function computes `feeCap` by dividing the fee amount by the gas limit: [1](#0-0) 

```go
gas := feeTx.GetGas()
...
feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
```

`sdkmath.Int.Quo` delegates to `math/big.Int.Quo`, which **panics** when the divisor is zero. There is no guard checking `gas == 0` before this line.

The same pattern exists in the legacy-fee fallback path `getTxPriority`: [2](#0-1) 

```go
func getTxPriority(fees sdk.Coins, gas int64) int64 {
    for _, fee := range fees {
        gasPrice := fee.Amount.QuoRaw(gas)
```

`QuoRaw(0)` also panics.

The only guards before line 83 are: [3](#0-2) 

None of these check whether `gas == 0`. A Cosmos SDK `Tx` encodes `AuthInfo.Fee.GasLimit` as a `uint64`; `ValidateBasic` does not reject `GasLimit = 0`, and `SetUpContextDecorator` merely sets a zero-capacity gas meter without aborting the ante chain before the fee checker runs.

---

### Impact Explanation

A Go `panic` in an ante handler propagates up through the ABCI `DeliverTx` / `CheckTx` call stack. In CometBFT, an unrecovered panic during `DeliverTx` causes the node process to crash. Because every validator node processes the same transaction, a single crafted transaction with `gas = 0` submitted to the mempool and included in a block causes **all validators to panic simultaneously**, halting the chain permanently until operators manually patch and restart.

This matches the **Critical** impact: *"Valid unprivileged transaction can halt the chain or cause deterministic validator consensus failure."*

---

### Likelihood Explanation

- No special privilege is required; any account can broadcast a Cosmos SDK transaction with `GasLimit = 0`.
- The EIP-1559 fee market is enabled by default (`DefaultNoBaseFee = false`, `DefaultEnableHeight = 0`). [4](#0-3) 

- The attacker only needs to craft a valid Cosmos tx (e.g., a `MsgSend`) with `fee.gas = 0` and submit it. No funds, no governance access, no validator collusion required.

---

### Recommendation

Add an explicit zero-gas guard at the top of the `NewDynamicFeeChecker` closure, before any division:

```go
gas := feeTx.GetGas()
if gas == 0 {
    return nil, 0, errorsmod.Wrap(errortypes.ErrInvalidRequest, "gas limit cannot be zero")
}
```

Apply the same guard in `checkTxFeeWithValidatorMinGasPrices` before calling `getTxPriority`:

```go
gas, err := ethermint.SafeInt64(feeTx.GetGas())
if err != nil || gas == 0 {
    return nil, 0, errorsmod.Wrap(errortypes.ErrInvalidRequest, "invalid gas limit")
}
```

---

### Proof of Concept

1. Construct a valid Cosmos SDK `Tx` (e.g., `MsgSend`) with `AuthInfo.Fee = { amount: [], gas_limit: 0 }`.
2. Broadcast it to any node with EIP-1559 enabled (the default).
3. During `CheckTx`, the ante chain reaches `NewDynamicFeeChecker` → line 83 executes `fee.Quo(sdkmath.NewIntFromUint64(0))` → `big.Int.Quo` panics with `"division by zero"`.
4. The panic is unrecovered; the node crashes. When the tx is included in a block and `DeliverTx` is called on every validator, all validators crash simultaneously, halting the chain. [5](#0-4) [6](#0-5)

### Citations

**File:** ante/evm/fee_checker.go (L49-60)
```go
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

**File:** ante/evm/fee_checker.go (L79-84)
```go
		gas := feeTx.GetGas()
		feeCoins := feeTx.GetFee()
		fee := feeCoins.AmountOf(denom)

		feeCap := fee.Quo(sdkmath.NewIntFromUint64(gas))
		baseFeeInt := sdkmath.NewIntFromBigInt(baseFee)
```

**File:** ante/evm/fee_checker.go (L153-159)
```go
func getTxPriority(fees sdk.Coins, gas int64) int64 {
	var priority int64

	for _, fee := range fees {
		gasPrice := fee.Amount.QuoRaw(gas)
		amt := gasPrice.Quo(types.DefaultPriorityReduction)
		p := int64(math.MaxInt64)
```

**File:** x/feemarket/types/params.go (L33-36)
```go
	DefaultEnableHeight = int64(0)
	// DefaultNoBaseFee is false
	DefaultNoBaseFee = false
)
```
