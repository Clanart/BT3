### Title
Missing nonzero validation on `Kip71GasTarget` governance parameter causes division-by-zero panic in block header validation - ([File: params/kip71_config.go])

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` when computing the next block's base fee, but unlike the sibling parameter `Kip71BaseFeeDenominator`, the governance parameter registration for `Kip71GasTarget` uses a `noopFormatChecker` that accepts any `uint64` value, including zero. If `GasTarget` is set to `0`, every subsequent block whose gas usage is nonzero will hit `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`, causing a `big.Int` division-by-zero panic. This mirrors the audited TWAMM bug class: a numeric input that is never checked against zero before being used as a divisor in a core state-transition computation, reachable through a value that is fully attacker/governance controlled.

### Finding Description
`NextMagmaBlockBaseFee` is the KIP-71 (Magma) base-fee formula used for every block after the Magma hardfork: [1](#0-0) 

When `parentGasUsed != gasTarget`, the code computes:
```
gasUsedDelta := ...
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divides by gasTarget
```
in both the "above target" and "below target" branches: [2](#0-1) 

Note that the code already defensively guards against `BaseFeeDenominator == 0` (line 71-76), substituting a fallback of `64`, but there is **no equivalent guard for `GasTarget == 0`**.

The governance parameter definitions in `kaiax/gov/param.go` show the asymmetry directly: `Kip71BaseFeeDenominator` explicitly rejects zero via its `FormatChecker`, while `Kip71GasTarget` uses `noopFormatChecker`, which accepts any value: [3](#0-2) 

`noopFormatChecker` simply returns `true` unconditionally: [4](#0-3) 

This value flows unchecked into `ParamSet.GasTarget` via `ParamSet.Set`: [5](#0-4) 

and is consumed directly in every header validation call: [6](#0-5) 

`GetParamSet` is also used by block proposers to compute the header's `BaseFee` when assembling new blocks, and by `feehistory.go`/`gasprice.go` RPC handlers, so the panic path is reachable both in consensus-critical header verification (every node validating every block) and in public RPC handlers that call `NextMagmaBlockBaseFee` (e.g. `oracle.processBlock`): [7](#0-6) 

### Impact Explanation
If `kip71.gastarget` is ever set to `0` through KIP-81 contract governance (`GovParam.setParam`/`setParamIn`) or header governance voting, the very next block whose `GasUsed` differs from `0` will cause `NextMagmaBlockBaseFee` to execute an unguarded `big.Int.Div` by zero. In Go, `big.Int.Div` panics on division by zero. Since this function is called from `BlockValidator.validateHeader`, which runs on every full node/CN validating every incoming block, and from the RPC fee-history/gas-price oracle, a value of `0` for this single governance field halts header validation and/or RPC service network-wide — a full-network liveness failure requiring a hotfix/hardfork to recover, directly analogous to the TWAMM report's "all pool operations revert" DoS caused by an unchecked divisor.

### Likelihood Explanation
Setting `GasTarget` requires the governance/owner privilege associated with `GovParam` contract governance (`onlyOwner`-gated `setParam`) or the header-governance voting mechanism, so it is not exploitable by a fully unprivileged transaction sender. However, per the assessment scope, "governance parameters" is an explicitly included analog category, since a single malformed or malicious governance parameter update (whether from a compromised/malicious governing node, or simple operator error) can brick the entire network with no additional validation catching it — there is no format-level protection analogous to the one already present for `BaseFeeDenominator`. The omission is a straightforward, easily reachable coding oversight (a missing `v != 0` check that exists for a sibling KIP-71 field but was forgotten for `GasTarget`), making this a low-effort, high-impact defect once a governance change is proposed.

### Recommendation
- Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0`, consistent with `Kip71BaseFeeDenominator`.
- Defensively guard `NextMagmaBlockBaseFee` in `params/kip71_config.go` against `GasTarget == 0` (e.g., short-circuit or apply a fallback default, matching the existing `BaseFeeDenominator == 0` fallback pattern) so that even a stale/invalid on-chain value cannot crash header validation.
- Add regression tests (as already exist for `BaseFeeDenominator == 0`) covering `GasTarget == 0` with nonzero `parentGasUsed`.

### Proof of Concept
1. Deploy or use the `GovParam` contract and, as its owner (or via header-governance voting as the governing node), call `setParam("kip71.gastarget", true, uint64(0)-encoded-bytes, activationBlock)`.
2. Once the parameter activates, mine/produce any block whose `GasUsed != 0` (trivial — any transaction).
3. On the next block, `BlockValidator.validateHeader` calls `govParamSet.ToKip71Config().VerifyMagmaHeader(...)` → `NextMagmaBlockBaseFee(...)`, which executes `x.Div(x, new(big.Int).SetUint64(0))`.
4. This panics with "division by zero" inside `math/big`, crashing (or causing a rejection loop in) every node performing header validation, and equally panics if triggered via the `eth_feeHistory` RPC path (`processBlock` → `NextMagmaBlockBaseFee`).

### Citations

**File:** params/kip71_config.go (L58-77)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
```

**File:** params/kip71_config.go (L88-128)
```go
	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
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

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

**File:** kaiax/gov/param.go (L310-333)
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
```

**File:** kaiax/gov/paramset.go (L77-78)
```go
	case Kip71GasTarget:
		p.GasTarget, ok = cv.(uint64)
```

**File:** blockchain/block_validator.go (L202-213)
```go
	// Verify Magma basefee rule from governance paramset.
	if v.config.IsMagmaForkEnabled(header.Number) {
		// Skip governance-dependent validation when gov module is not registered.
		if v.mGov != nil {
			govParamSet := v.mGov.GetParamSet(header.Number.Uint64())
			if err := govParamSet.ToKip71Config().VerifyMagmaHeader(header.BaseFee, parent.Number, parent.BaseFee, parent.GasUsed); err != nil {
				return err
			}
		}
	} else if header.BaseFee != nil {
		return ErrInvalidBaseFee
	}
```

**File:** node/cn/gasprice/feehistory.go (L111-115)
```go
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
