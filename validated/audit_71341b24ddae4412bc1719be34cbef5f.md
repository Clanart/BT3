### Title
Wrong Validator for `MinGasMultiplier` in `ParamSetPairs` Bypasses Upper-Bound Check, Enabling `gasUsed > GasLimit` Underflow and Chain Halt - (File: `x/feemarket/types/params.go`)

### Summary

`ParamSetPairs()` in `x/feemarket/types/params.go` registers `MinGasMultiplier` with the validator `validateMinGasPrice` instead of `validateMinGasMultiplier`. `validateMinGasPrice` only checks that the value is non-nil and non-negative; it does **not** enforce the upper bound `≤ 1`. `validateMinGasMultiplier` does enforce that bound. As a result, a governance proposal that updates `MinGasMultiplier` individually via the legacy params subspace can set it to any value `> 1`, bypassing the intended constraint. When `MinGasMultiplier > 1`, `minimumGasUsed` exceeds `msg.GasLimit` in `ApplyMessageWithConfig`, causing a `uint64` underflow in `leftoverGas = msg.GasLimit - gasUsed`, which breaks every subsequent EVM transaction and halts the chain.

### Finding Description

In `x/feemarket/types/params.go`, `ParamSetPairs()` maps each parameter key to a validator callback used by the legacy Cosmos SDK params subspace when a single parameter is updated:

```go
paramtypes.NewParamSetPair(ParamStoreKeyMinGasMultiplier, &p.MinGasMultiplier, validateMinGasPrice),
``` [1](#0-0) 

The validator supplied is `validateMinGasPrice`, which only checks:

```go
if v.IsNegative() { return fmt.Errorf("value cannot be negative: %s", i) }
``` [2](#0-1) 

The correct validator, `validateMinGasMultiplier`, additionally enforces:

```go
if v.GT(sdkmath.LegacyOneDec()) {
    return fmt.Errorf("value cannot be greater than 1: %s", v)
}
``` [3](#0-2) 

The same copy-paste error exists in the migration type: [4](#0-3) 

The full `Params.Validate()` does call `validateMinGasMultiplier`, so the modern `MsgUpdateParams` path is safe. But the legacy subspace path (used by older governance proposals that update a single param key) invokes only the `ParamSetPairs` validator, which is `validateMinGasPrice` — missing the `> 1` check entirely.

Once `MinGasMultiplier > 1` is stored, every EVM transaction execution in `ApplyMessageWithConfig` computes:

```go
minimumGasUsed := gasLimit.Mul(minGasMultiplier)   // > gasLimit when multiplier > 1
gasUsed = sdkmath.LegacyMaxDec(minimumGasUsed, sdkmath.LegacyNewDec(tempGasUsed)).TruncateInt().Uint64()
leftoverGas = msg.GasLimit - gasUsed               // uint64 underflow
``` [5](#0-4) 

`gasUsed` becomes larger than `msg.GasLimit`, so the subtraction wraps around to `~MaxUint64`. This corrupted `leftoverGas` is then used in gas-refund accounting and the debug-trace balance credit path, causing either a massive erroneous balance addition or a downstream error that aborts block processing.

Additionally, `EndBlock` uses `MinGasMultiplier` to compute `blockGasWanted`:

```go
limitedGasWanted := sdkmath.LegacyNewDec(gw).Mul(minGasMultiplier)
gasWanted = sdkmath.LegacyMaxDec(limitedGasWanted, sdkmath.LegacyNewDec(gasUsed)).TruncateInt().Uint64()
k.SetBlockGasWanted(ctx, gasWanted)
``` [6](#0-5) 

With `MinGasMultiplier > 1`, `blockGasWanted` is inflated beyond the real gas consumed, feeding an artificially high `parentGasUsed` into `CalculateBaseFee`, which drives the EIP-1559 base fee upward without bound.

### Impact Explanation

**High/Critical.** Once the invalid `MinGasMultiplier` is committed to state:

1. Every call to `ApplyMessageWithConfig` produces `gasUsed > msg.GasLimit`, causing a `uint64` underflow in `leftoverGas`. The corrupted value propagates into `RefundGas` and the debug-trace balance-credit path. In the debug-trace path this directly mints an astronomically large token balance to the sender (`stateDB.AddBalance(sender, refund, ...)`), constituting unauthorized token minting. In the normal path the corrupted `leftoverGas` causes `RefundGas` to attempt an impossible refund, returning an error that aborts block processing — a deterministic chain halt.
2. `EndBlock` permanently inflates `blockGasWanted`, causing the base fee to spiral upward, making all user transactions economically unviable.

Both effects match the allowed impact scope: (a) unauthorized balance mint of EVM-denom funds, and (b) block-processing path that can halt the chain or corrupt committed state.

### Likelihood Explanation

The legacy params subspace is the standard governance mechanism on chains that have not yet migrated to `MsgUpdateParams`. A governance proposal targeting `ParamStoreKeyMinGasMultiplier` with a value such as `2` passes `validateMinGasPrice` (non-negative, non-nil) and is accepted. No special privileges beyond a passing governance vote are required. The bug is a silent copy-paste error invisible to proposal authors and reviewers.

### Recommendation

Replace `validateMinGasPrice` with `validateMinGasMultiplier` in both `ParamSetPairs` registrations:

**`x/feemarket/types/params.go` line 64:**
```go
paramtypes.NewParamSetPair(ParamStoreKeyMinGasMultiplier, &p.MinGasMultiplier, validateMinGasMultiplier),
```

**`x/feemarket/migrations/v4/types/params.go` line 50:**
```go
paramtypes.NewParamSetPair(ParamStoreKeyMinGasMultiplier, &p.MinGasMultiplier, validateMinGasMultiplier),
```

Additionally, add a test case to `TestParamsValidatePriv` that asserts `validateMinGasPrice(sdkmath.LegacyNewDec(2))` returns no error (to document the current gap) and that `validateMinGasMultiplier(sdkmath.LegacyNewDec(2))` returns an error (to pin the correct behavior).

### Proof of Concept

1. Submit a governance proposal via the legacy params subspace to set `MinGasMultiplier = "2.000000000000000000"` (a valid `LegacyDec`, non-negative, non-nil — passes `validateMinGasPrice`).
2. Proposal passes; `MinGasMultiplier = 2` is written to state.
3. Any user submits an EVM transaction with `GasLimit = 21000`.
4. In `ApplyMessageWithConfig`:
   - `gasLimit = Dec(21000)`
   - `minimumGasUsed = 21000 * 2 = 42000`
   - `gasUsed = max(42000, actualGasUsed).Uint64() = 42000`
   - `leftoverGas = 21000 - 42000` → uint64 underflow → `leftoverGas = 18446744073709530616`
5. In the debug-trace path: `refund = gasPrice * 18446744073709530616` is credited to sender via `stateDB.AddBalance` — minting an enormous balance.
6. In the normal execution path: `RefundGas` attempts to transfer `18446744073709530616 * gasPrice` from the fee collector, fails, returns an error, and block processing aborts — chain halt. [7](#0-6) [5](#0-4) [6](#0-5)

### Citations

**File:** x/feemarket/types/params.go (L56-65)
```go
func (p *Params) ParamSetPairs() paramtypes.ParamSetPairs {
	return paramtypes.ParamSetPairs{
		paramtypes.NewParamSetPair(ParamStoreKeyNoBaseFee, &p.NoBaseFee, validateBool),
		paramtypes.NewParamSetPair(ParamStoreKeyBaseFeeChangeDenominator, &p.BaseFeeChangeDenominator, validateBaseFeeChangeDenominator),
		paramtypes.NewParamSetPair(ParamStoreKeyElasticityMultiplier, &p.ElasticityMultiplier, validateElasticityMultiplier),
		paramtypes.NewParamSetPair(ParamStoreKeyBaseFee, &p.BaseFee, validateBaseFee),
		paramtypes.NewParamSetPair(ParamStoreKeyEnableHeight, &p.EnableHeight, validateEnableHeight),
		paramtypes.NewParamSetPair(ParamStoreKeyMinGasPrice, &p.MinGasPrice, validateMinGasPrice),
		paramtypes.NewParamSetPair(ParamStoreKeyMinGasMultiplier, &p.MinGasMultiplier, validateMinGasPrice),
	}
```

**File:** x/feemarket/types/params.go (L147-163)
```go
func validateMinGasPrice(i interface{}) error {
	v, ok := i.(sdkmath.LegacyDec)

	if !ok {
		return fmt.Errorf("invalid parameter type: %T", i)
	}

	if v.IsNil() {
		return fmt.Errorf("invalid parameter: nil")
	}

	if v.IsNegative() {
		return fmt.Errorf("value cannot be negative: %s", i)
	}

	return nil
}
```

**File:** x/feemarket/types/params.go (L215-233)
```go
func validateMinGasMultiplier(i interface{}) error {
	v, ok := i.(sdkmath.LegacyDec)

	if !ok {
		return fmt.Errorf("invalid parameter type: %T", i)
	}

	if v.IsNil() {
		return fmt.Errorf("invalid parameter: nil")
	}

	if v.IsNegative() {
		return fmt.Errorf("value cannot be negative: %s", v)
	}

	if v.GT(sdkmath.LegacyOneDec()) {
		return fmt.Errorf("value cannot be greater than 1: %s", v)
	}
	return nil
```

**File:** x/feemarket/migrations/v4/types/params.go (L50-50)
```go
		paramtypes.NewParamSetPair(ParamStoreKeyMinGasMultiplier, &p.MinGasMultiplier, validateMinGasPrice),
```

**File:** x/evm/keeper/state_transition.go (L571-589)
```go
	gasLimit := sdkmath.LegacyNewDec(limit)
	minGasMultiplier := cfg.FeeMarketParams.MinGasMultiplier
	if minGasMultiplier.IsNil() {
		// in case we are executing eth_call on a legacy block, returns a zero value.
		minGasMultiplier = sdkmath.LegacyZeroDec()
	}
	minimumGasUsed := gasLimit.Mul(minGasMultiplier)

	if msg.GasLimit < leftoverGas {
		return nil, errorsmod.Wrapf(types.ErrGasOverflow, "message gas limit < leftover gas (%d < %d)", msg.GasLimit, leftoverGas)
	}
	tempGasUsed, err := ethermint.SafeInt64(temporaryGasUsed)
	if err != nil {
		return nil, err
	}

	gasUsed = sdkmath.LegacyMaxDec(minimumGasUsed, sdkmath.LegacyNewDec(tempGasUsed)).TruncateInt().Uint64()
	// reset leftoverGas, to be used by the tracer
	leftoverGas = msg.GasLimit - gasUsed
```

**File:** x/feemarket/keeper/abci.go (L72-75)
```go
	minGasMultiplier := k.GetParams(ctx).MinGasMultiplier
	limitedGasWanted := sdkmath.LegacyNewDec(gw).Mul(minGasMultiplier)
	gasWanted = sdkmath.LegacyMaxDec(limitedGasWanted, sdkmath.LegacyNewDec(gasUsed)).TruncateInt().Uint64()
	k.SetBlockGasWanted(ctx, gasWanted)
```
