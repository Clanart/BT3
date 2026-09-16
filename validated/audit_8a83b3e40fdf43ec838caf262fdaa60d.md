Confirmed: `Kip71GasTarget` uses `noopFormatChecker` (`kaiax/gov/param.go:324-334`), meaning governance votes can set `kip71.gastarget` to `0` with no validation, unlike `Kip71BaseFeeDenominator` which explicitly checks `v != 0` (`kaiax/gov/param.go:310-315`). This asymmetry is the root cause of a reachable division-by-zero panic in the base-fee computation used by every block's header validation and gas pricing.

### Title
Governance can set `kip71.gastarget = 0`, causing division-by-zero panic in `NextMagmaBlockBaseFee` on every subsequent block - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.GasTarget` is a governance-controlled parameter with no non-zero validation. If it is voted to `0`, `NextMagmaBlockBaseFee()` — called on every block to compute and verify `baseFee` — divides by `gasTarget` and panics, halting block production/validation network-wide.

### Finding Description
`Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, which always returns `true` regardless of the submitted value [1](#0-0) . This contrasts with the sibling parameter `Kip71BaseFeeDenominator`, whose checker explicitly rejects `0` because the code guards against a zero denominator by falling back to `64` [2](#0-1) [3](#0-2) . No equivalent guard exists for `GasTarget`.

In `NextMagmaBlockBaseFee`, `gasTarget := kc.GasTarget` is used unguarded. If `parentGasUsed != gasTarget` (which is virtually guaranteed once `gasTarget == 0` and any gas is used), the code enters either the "gas used > target" branch or is trivially in that branch since any nonzero usage exceeds `0`:
```
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divide by 0 → panic
``` [4](#0-3) 

`math/big.Int.Div` panics with "division by zero" when the divisor is zero, and there is no recover anywhere in this call chain. This function is invoked from `VerifyMagmaHeader`, which every node calls to validate the `baseFee` field of every incoming block header [5](#0-4) , and from block/txpool base-fee estimation paths used for every transaction submission and gas suggestion (confirmed via `kip71_config_test.go` and `simulated_test.go` usage of `NextMagmaBlockBaseFee`) [6](#0-5) .

This is analogous to the Tapioca `debtStartPoint` bug class: an admin/governance-supplied configuration value that is not validated against a boundary condition, causing a shared, unconditionally-invoked accounting function to permanently revert/panic for every subsequent caller (here, every block, rather than the first borrower's collateral).

### Impact Explanation
Once `kip71.gastarget` is voted to `0` and takes effect, any block with `parentGasUsed > 0` causes `NextMagmaBlockBaseFee` to panic. Because this function is called by all honest nodes during header verification and by RPC layers computing suggested gas price, the panic occurs deterministically and identically on every node, effectively halting the chain (denial of service network-wide) until the governance parameter is corrected or the binary is patched. This is a chain-halting availability bug reachable purely through the normal, permitted governance parameter-setting path (no malicious validator/node/p2p behavior required), matching the "governance parameters" reachable surface allowed by the rules.

### Likelihood Explanation
Requires a governance vote/decision to set `GasTarget` to `0` (or the parameter to otherwise reach `0`, e.g., a misconfigured default or migration). This is not an attacker-controlled single transaction, but it is a legitimate, unprivileged-reachable configuration path with no validation whatsoever, unlike the parallel `BaseFeeDenominator` field which is explicitly protected. Governance misconfiguration (accidental or malicious governing-node vote) is a realistic and previously demonstrated bug class (the very existence of a guard for `BaseFeeDenominator` but not `GasTarget` suggests this was overlooked, not intentionally accepted risk).

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0`, mirroring the existing check on `Kip71BaseFeeDenominator`:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
}
```
Additionally, consider adding a defensive zero-check inside `NextMagmaBlockBaseFee` itself (similar to the `baseFeeDenominator` fallback) so that even a pre-existing/legacy `ChainConfig` with `GasTarget == 0` cannot panic.

### Proof of Concept
1. Governance (governing node under `single` mode, or majority vote under `none` mode) submits a vote setting `kip71.gastarget` to `0`.
2. `Kip71GasTarget.FormatChecker` (`noopFormatChecker`) accepts the vote unconditionally [1](#0-0) .
3. Once the vote is finalized/applied, the next block header's baseFee must be computed via `NextMagmaBlockBaseFee` with the new `GasTarget = 0`.
4. Any block where `parentGasUsed > 0` (i.e., a block containing at least one transaction) hits `x.Div(x, new(big.Int).SetUint64(0))` in the "gas used > target" branch, which panics `params/kip71_config.go:100-103`.
5. This function is called from `VerifyMagmaHeader` during block validation by every node, so the panic occurs network-wide, crashing/halting node processes (or, if recovered by caller framework, invalidating every block indefinitely).

### Citations

**File:** kaiax/gov/param.go (L310-323)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.BaseFeeDenominator, nil
		},
		DefaultValue: uint64(20),
	},
```

**File:** kaiax/gov/param.go (L324-334)
```go
	Kip71GasTarget: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.GasTarget, nil
		},
		DefaultValue: uint64(30000000),
	},
```

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
```

**File:** params/kip71_config.go (L70-76)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
```

**File:** params/kip71_config.go (L92-103)
```go
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config_test.go (L115-132)
```go
func TestNextBlockBaseFee(t *testing.T) {
	tests := []struct {
		parentBaseFee int64
		parentGasUsed uint64
		nextBaseFee   int64
	}{
		{750000000000, 30000000, 750000000000}, // usage == target
		{30000000000, 20000000, 29500000000},   // usage below target
		{300000000000, 40000000, 305000000000}, // usage above target
	}
	for i, test := range tests {
		have := getTestConfig(common.Big3).Governance.KIP71.NextMagmaBlockBaseFee(common.Big3, big.NewInt(test.parentBaseFee), test.parentGasUsed)
		want := big.NewInt(test.nextBaseFee)
		if have.Cmp(want) != 0 {
			t.Errorf("test %d: have %d  want %d, ", i, have, want)
		}
	}
}
```
