### Title
GasTarget governance parameter accepts zero, causing division-by-zero panic in KIP-71 base fee calculation - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` without any zero-check, while the sibling `BaseFeeDenominator` field is explicitly guarded against a zero value. The governance parameter validator for `GasTarget` (`Kip71GasTarget` in `kaiax/gov/param.go`) uses `noopFormatChecker`, which allows any `uint64` value, including `0`, to be accepted as a valid governance vote/parameter update. This mirrors the exact bug class from the external report: a divisor value that is nominally allowed to be a degenerate value (`0`) but has no safe code path, leading to a division-by-zero panic instead of the Notional `require`/revert.

### Finding Description
`NextMagmaBlockBaseFee` is the core KIP-71 dynamic base fee computation used both for verifying block headers (`VerifyMagmaHeader`) and for suggesting gas prices (fee history / gas price oracle): [1](#0-0) 

For `BaseFeeDenominator`, the code explicitly protects against `0`: [2](#0-1) 

But for `GasTarget`, there is no equivalent protection, and it is used directly as a divisor in both the "gas used above target" and "gas used below target" branches: [3](#0-2) [4](#0-3) 

The `math/big.Int.Div` call panics at runtime ("division by zero") when the divisor is zero. Because `parentGasUsed == gasTarget` is checked earlier (line 90), the panic path is only reached when `parentGasUsed != 0`, i.e., whenever the parent block used any gas at all with `GasTarget == 0` — a state that will occur on virtually every non-empty block once `GasTarget` is set to `0`.

The `GasTarget` governance parameter's format checker performs no validation at all: [5](#0-4) 

This is inconsistent with the neighboring `Kip71BaseFeeDenominator` parameter, which explicitly rejects zero: [6](#0-5) 

Once a governance vote sets `GasTarget` to `0` and it is included in a block header vote, the resulting `ParamSet` (via `ToKip71Config`) is used for subsequent base-fee derivation across the network: [7](#0-6) 

### Impact Explanation
This is reachable via a standard governance parameter update path (allowed reachable surface per the audit scope: "governance parameters"). Once activated, every node that computes `NextMagmaBlockBaseFee` on a block with nonzero gas usage — which is the normal case — will panic and crash, since `big.Int.Div` panics on division by zero. This affects:
- Block header verification (`VerifyMagmaHeader`), meaning all full nodes trying to validate blocks after the parameter takes effect would crash, halting the chain (denial of service across the network / state divergence, since nodes with different governance parameter caches or timing could crash while others do not).
- The gas price oracle / fee history endpoints (`node/cn/gasprice/gasprice.go`, `feehistory.go`), causing RPC-serving nodes to crash on public RPC calls (`eth_gasPrice`, `eth_feeHistory`) once the effective parameter set includes `GasTarget = 0`.

This is a network-wide safety issue caused by a missing input validation in a governance-settable consensus parameter and satisfies the "acceptance of an invalid ... value / state divergence between honest nodes" criteria, since a value that should never pass validation is silently accepted and later causes an unrecoverable runtime panic rather than a controlled rejection.

### Likelihood Explanation
Likelihood depends on governance being willing/able to set `GasTarget` to `0` (via the standard `kaiax/gov` vote/parameter mechanism, which is an in-scope reachable category per the audit rules). There is no additional privileged system compromise required beyond using the existing governance parameter update mechanism — the validation logic itself is the vulnerability. Given `Kip71BaseFeeDenominator` already anticipates and guards this exact class of misconfiguration, and `Kip71GasTarget` does not, this strongly suggests an oversight rather than an intentional design decision, making it a credible configuration/bug-class issue.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (analogous to `Kip71BaseFeeDenominator`) that rejects `0`:
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
Additionally, as defense in depth, add the same zero-guard used for `BaseFeeDenominator` directly in `NextMagmaBlockBaseFee` before using `kc.GasTarget` as a divisor, to prevent a panic even if an invalid value reaches the config through another path (e.g. genesis/chain config file misconfiguration).

### Proof of Concept
1. Through the governance parameter voting mechanism, submit and pass a vote setting `Kip71GasTarget` (i.e. `governance.kip71.gastarget`) to `0`. The `noopFormatChecker` [5](#0-4)  accepts this value without rejection.
2. Once the parameter takes effect at the next governance epoch, any block with nonzero gas usage causes `NextMagmaBlockBaseFee` to execute the `parentGasUsed > gasTarget` (or `<`) branch [8](#0-7) .
3. `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0` triggers a native Go panic ("division by zero") in `math/big`.
4. This panic occurs during block header verification (`VerifyMagmaHeader`) on every full node, and separately during `eth_gasPrice`/`eth_feeHistory` RPC calls (`node/cn/gasprice/feehistory.go` `processBlock`, which also calls `NextMagmaBlockBaseFee` [9](#0-8) ), crashing/halting nodes network-wide.

### Citations

**File:** params/kip71_config.go (L58-79)
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
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

```

**File:** params/kip71_config.go (L92-121)
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

**File:** kaiax/gov/paramset.go (L199-207)
```go
func (p *ParamSet) ToKip71Config() *params.KIP71Config {
	return &params.KIP71Config{
		LowerBoundBaseFee:         p.LowerBoundBaseFee,
		UpperBoundBaseFee:         p.UpperBoundBaseFee,
		GasTarget:                 p.GasTarget,
		MaxBlockGasUsedForBaseFee: p.MaxBlockGasUsedForBaseFee,
		BaseFeeDenominator:        p.BaseFeeDenominator,
	}
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
