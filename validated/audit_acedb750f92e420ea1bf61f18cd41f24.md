Confirmed: `Kip71GasTarget` uses `noopFormatChecker`, meaning a governance vote can set `reward.kip71.gastarget` (`Kip71GasTarget`) to `0`, unlike `Kip71BaseFeeDenominator` which explicitly rejects `0` via its `FormatChecker` at [1](#0-0) . `GasTarget` has no such protection, as shown at [2](#0-1) .

### Title
Zero `GasTarget` governance parameter causes division-by-zero panic in `NextMagmaBlockBaseFee`, halting all nodes' block/state processing - (File: params/kip71_config.go)

### Summary
`NextMagmaBlockBaseFee` (analogous to `calculate_rewards`'s `checked_mul_floor` division in the external report) divides by the governance-controlled `gasTarget` value without checking it for zero, unlike the adjacent `baseFeeDenominator` which explicitly special-cases zero to avoid a panic.

### Finding Description
`kc.NextMagmaBlockBaseFee` performs `x.Div(x, new(big.Int).SetUint64(gasTarget))` in both the "gas used above target" and "gas used below target" branches [3](#0-2) . `gasTarget := kc.GasTarget` is taken directly from `KIP71Config.GasTarget` with no zero-guard, in contrast to `BaseFeeDenominator`, which is explicitly checked and defaulted to `64` if zero [4](#0-3) .

The `GasTarget` governance parameter (`Kip71GasTarget`) is registered with `noopFormatChecker`, meaning governance votes can set it to `0` with no validation, unlike `Kip71BaseFeeDenominator` whose `FormatChecker` explicitly rejects `v == 0` [5](#0-4) . Go's `big.Int.Div` panics with "division by zero" when the divisor is zero — this is the exact bug class described in the report (division by a value that can be zero, not guarded at the site of the division).

`NextMagmaBlockBaseFee` is called both during header verification (`VerifyMagmaHeader`, reachable by any peer/producer submitting a block with a `baseFee` field) and during block assembly/finalization for every block after the Magma hardfork, since the parameter is read via `GovModule.GetParamSet` each block [6](#0-5) .

### Impact Explanation
If `GasTarget` is voted to `0` (or otherwise becomes `0`, e.g. governance misconfiguration or malicious governance proposal that passes), then on the very next block where `parentGasUsed != gasTarget` (i.e., any block with nonzero gas usage, since `gasTarget=0`), every node computing `NextMagmaBlockBaseFee` — during block verification, block building, or the `kaia_getReward`/fee-history RPC path — will panic. This is a state-transition/consensus-critical function; an unrecoverable panic here would crash node processes chain-wide, i.e., a full network halt, not merely an unclaimable reward for one user. This is more severe than the original farm-manager finding (locked rewards for one user) because it can DoS the entire chain (all full nodes, validators, RPC nodes) once the parameter reaches zero.

### Likelihood Explanation
Reaching this state requires the `reward.kip71.gastarget` governance parameter to be set to `0` via a governance proposal/vote, which is an accepted, in-scope path ("governance parameters" is explicitly listed as reachable in the given rules). Since no `FormatChecker` rejects `0` (unlike the sibling `BaseFeeDenominator` parameter which explicitly does), a single passing governance vote — or an operator/CLI misconfiguration inherited into governance state — is sufficient to trigger this on the following block with nonzero gas usage. Given the asymmetric treatment (denominator guarded, gas target not), this looks like an unintentional oversight rather than an intentionally safe design.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0`, consistent with `Kip71BaseFeeDenominator`. Additionally, as defense-in-depth, guard the division site in `NextMagmaBlockBaseFee` itself (mirroring the `BaseFeeDenominator == 0` fallback) so a stray zero value cannot panic block processing.

### Proof of Concept
1. Governance vote sets `reward.kip71.gastarget` (`Kip71GasTarget`) to `0`; `noopFormatChecker` accepts it (see [2](#0-1) ), so it becomes effective in the `KIP71Config` used by `GetParamSet`.
2. On the next block after the Magma hardfork with `parentHeaderGasUsed != 0`, `NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` is invoked (e.g., from header verification or block finalization).
3. Since `parentGasUsed (>0) != gasTarget (0)`, execution enters the `parentGasUsed > gasTarget` branch: `gasUsedDelta := parentGasUsed - 0`; `x := parentBaseFee * gasUsedDelta`; `y := x.Div(x, new(big.Int).SetUint64(0))` — this line panics with "division by zero" [7](#0-6) .
4. Every node executing this code path (validators verifying/building blocks, full nodes syncing, RPC nodes serving `kaia_getReward`) panics, halting chain progress network-wide.

### Citations

**File:** kaiax/gov/param.go (L310-334)
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

**File:** params/kip71_config.go (L99-121)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```
