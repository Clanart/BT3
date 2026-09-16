### Title
Missing zero-value validation on `kip71.lowerboundbasefee` / `governance.unitprice` governance parameters allows base fee to be driven to zero - ([File: kaiax/gov/param.go])

### Summary
The `kip71.lowerboundbasefee`, `kip71.gastarget`, `kip71.upperboundbasefee`, and `governance.unitprice` governance parameters use `noopFormatChecker`, which accepts any value including `0`, while the sibling parameter `kip71.basefeedenominator` explicitly rejects zero (`v != 0`). This asymmetry mirrors the reported `minFee` bug class: a numeric fee-controlling parameter can be accidentally (or intentionally) set to `0`, and unprivileged transaction senders can then exploit the resulting free-transaction window.

### Finding Description
In [1](#0-0) , `GovernanceUnitPrice` uses `noopFormatChecker` with no lower bound. Similarly, `Kip71LowerBoundBaseFee`, `Kip71GasTarget`, and `Kip71UpperBoundBaseFee` all use `noopFormatChecker` as seen in [2](#0-1) , whereas `Kip71BaseFeeDenominator` explicitly guards against zero: [3](#0-2) 

The consensus-level consistency check for these votes, `checkConsistency` in `headerGovModule`, only compares `Kip71LowerBoundBaseFee` against `UpperBoundBaseFee` and vice versa - it never rejects zero: [4](#0-3) 

Once `LowerBoundBaseFee` (or `UnitPrice` pre-Magma) is `0`, the KIP-71 dynamic fee calculation in `NextMagmaBlockBaseFee` will float the base fee down to `0` under low network utilization, since the lower bound itself is the floor: [5](#0-4) [6](#0-5) 

At that point, `SuggestTipCap`/`SuggestPrice` in the gas price oracle will also suggest `0`, and any unprivileged transaction sender can submit ordinary transactions paying `baseFee * gasUsed == 0`, i.e., paying no network fee at all: [7](#0-6) 

This is functionally identical to the reported Sherlock finding: a fee-controlling numeric setter with no non-zero validation, whose accidental zero-initialization is silently accepted and directly exploitable by ordinary transaction senders once in effect.

### Impact Explanation
The KIP-71 mechanism (Magma/Kaia fee model) burns the base fee portion of every transaction as part of the chain's fee-burn/deflation and validator/PoC revenue model. If `LowerBoundBaseFee` (or pre-Magma `UnitPrice`) is set to `0` — whether by governance error or a validator/owner mistake with no code-level safety net — every subsequent transaction submitted by any unprivileged sender pays zero base fee until the parameter is corrected. This is a direct, sustained loss of protocol fee revenue and burn accounting for as long as the misconfiguration lasts, and it is trivially reachable by a normal transaction sender simply broadcasting transactions during that window.

### Likelihood Explanation
The parameter is validator/GC-governed (via header votes or `GovParam.setParamIn`), so it requires a privileged actor to set the value; however, unlike `Kip71BaseFeeDenominator`, there is no defense-in-depth format check preventing `0`, so a single mistaken vote/parameter update (which the codebase explicitly guards against for the denominator but not for the lower bound/unit price) fully exposes the network. Once set, exploitation by ordinary tx senders is immediate, deterministic, and requires no special privilege.

### Recommendation
Add a non-zero (and ideally minimum-floor) `FormatChecker` for `GovernanceUnitPrice`, `Kip71LowerBoundBaseFee`, `Kip71GasTarget`, and `Kip71UpperBoundBaseFee` in [1](#0-0)  and [2](#0-1) , consistent with the existing `v != 0` check already applied to `Kip71BaseFeeDenominator`. Additionally, `checkConsistency` in [4](#0-3)  should reject a `LowerBoundBaseFee` vote of `0`.

### Proof of Concept
1. A GC member/owner submits a governance vote (header vote or `GovParam.setParamIn`) setting `kip71.lowerboundbasefee = 0` (or `governance.unitprice = 0` pre-Magma).
2. `checkConsistency` in `header.go` only checks `vote.Value() > params.UpperBoundBaseFee`; `0` passes.
3. `Params[Kip71LowerBoundBaseFee].FormatChecker` is `noopFormatChecker`, so it is accepted unconditionally, per [8](#0-7) .
4. Once activated, `NextMagmaBlockBaseFee` in `params/kip71_config.go` computes the base fee floor as `0`, and any unprivileged sender's ordinary transactions are accepted paying zero base fee until the value is corrected via another governance vote.

### Citations

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
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

**File:** kaiax/gov/param.go (L335-357)
```go
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
	Kip71MaxBlockGasUsedForBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.MaxBlockGasUsedForBaseFee, nil
		},
		DefaultValue: uint64(60000000),
	},
	Kip71UpperBoundBaseFee: {
```

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
```

**File:** params/kip71_config.go (L58-68)
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
```

**File:** params/kip71_config.go (L110-128)
```go
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

**File:** node/cn/gasprice/gasprice.go (L329-336)
```go
// isRelaxedNetwork returns true if the current network congestion is low to the point
// paying any tip is unnecessary. It returns true when the head block is after Magma fork
// and the next base fee is at the lower bound.
func (oracle *Oracle) isRelaxedNetwork(header *types.Header) bool {
	pset := oracle.govModule.GetParamSet(header.Number.Uint64() + 1)
	nextBaseFee := pset.ToKip71Config().NextMagmaBlockBaseFee(header.Number, header.BaseFee, header.GasUsed)
	return nextBaseFee.Cmp(big.NewInt(int64(pset.LowerBoundBaseFee))) <= 0
}
```
