### Title
Governance-settable `Kip71GasTarget = 0` causes unrecovered division-by-zero panic in `NextMagmaBlockBaseFee` - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee()` divides by `kc.GasTarget` when the parent block's gas usage differs from the target, without ever checking that `GasTarget` is non-zero. Unlike `BaseFeeDenominator`, which has an explicit zero-fallback, `GasTarget` has no such guard, and the governance parameter validator for `Kip71GasTarget` uses a no-op format checker that accepts any `uint64` value, including `0`.

### Finding Description
`NextMagmaBlockBaseFee` computes the delta to the base fee using `gasTarget := kc.GasTarget` as a divisor: [1](#0-0) [2](#0-1) [3](#0-2) 

Note that `BaseFeeDenominator` is explicitly checked for zero and replaced with a safe default of 64 to "avoid panic": [1](#0-0) 

No equivalent check exists for `gasTarget`. When `parentGasUsed != gasTarget` (the common case for any nonzero/nonequal gas usage), the code performs `x.Div(x, new(big.Int).SetUint64(gasTarget))` at line 102 (gas-used-above-target branch) or line 120 (gas-used-below-target branch). If `gasTarget == 0`, `big.Int.Div` panics with a Go runtime "division by zero" error, which is not caught anywhere in the call path.

Crucially, the governance parameter `Kip71GasTarget` is validated with `FormatChecker: noopFormatChecker`, which places no constraint on the value (unlike `Kip71BaseFeeDenominator`, whose checker explicitly requires `v != 0`): [4](#0-3) 

This means governance voting (via `GovParam` contract voting, or header-vote mechanism) can set `Kip71GasTarget` to `0` without rejection at the vote-validation layer, since `NewVoteData` only enforces `param.Canonicalizer` + `param.FormatChecker`: [5](#0-4) 

`NextMagmaBlockBaseFee` is invoked both when building a new block header (`makeHeader`) and when verifying an existing block's `BaseFee` (`VerifyMagmaHeader`), so this affects both block-production and block-validation code paths: [6](#0-5) [7](#0-6) 

### Impact Explanation
Once governance sets `GasTarget = 0` (e.g., through a successful governance vote, which is squarely in the "governance parameters" attack surface), every subsequent block whose `parentGasUsed != 0` (i.e., virtually every real-world block, since the exact-match `parentGasUsed == gasTarget == 0` shortcut only applies to literally empty blocks) will trigger a division-by-zero panic inside `NextMagmaBlockBaseFee`. This function executes on the hot path of block assembly and header verification for every Kaia node applying the Magma base-fee mechanism, so the panic would crash/halt block production and validation network-wide — a consensus-critical denial of service, not merely a single transaction revert. This is a state-transition/consensus-halting bug reachable purely through the in-scope "governance parameters" surface.

### Likelihood Explanation
Likelihood depends on governance being able to actually push `Kip71GasTarget = 0` through consensus. Since the parameter's `FormatChecker` is a no-op (accepting any `uint64`), nothing in the vote-validation or parameter-application code specifically rejects zero, in contrast to the deliberate `v != 0` check applied to `BaseFeeDenominator`. This asymmetry strongly suggests the zero-check for `GasTarget` was simply omitted, making this a plausible and reachable configuration/governance mistake (or malicious governing-node action in single-governance mode) rather than a purely theoretical scenario.

### Recommendation
Add an explicit non-zero format-check for `Kip71GasTarget` matching the one already applied to `Kip71BaseFeeDenominator` (e.g., `FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }`), and additionally add a defensive zero-check inside `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator` zero-fallback) so that even a pre-existing/legacy chain config with `GasTarget == 0` cannot crash the node.

### Proof of Concept
1. Governance (or a governing node in `single` mode) submits/approves a vote setting `Kip71GasTarget` to `0`. Because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, the vote passes canonicalization/format validation in `NewVoteData` and is applied to the chain config's `KIP71Config.GasTarget`.
2. On the next block after activation, any block with nonzero gas usage (`parentGasUsed != gasTarget`, trivially true since `gasTarget == 0` and almost any transaction uses gas) causes `makeHeader`/`VerifyMagmaHeader` to call `NextMagmaBlockBaseFee`.
3. Inside `NextMagmaBlockBaseFee`, `gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)` followed by `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` executes `big.Int.Div(x, 0)`, which panics at runtime with "division by zero", crashing the node process during block assembly/verification.

### Citations

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

**File:** params/kip71_config.go (L97-103)
```go
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L115-121)
```go
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
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

**File:** kaiax/gov/headergov/vote.go (L29-48)
```go
func NewVoteData(voter common.Address, name string, value any) VoteData {
	param, ok := gov.Params[gov.ParamName(name)]
	if !ok {
		param, ok = gov.ValidatorParams[gov.ParamName(name)]
		if !ok {
			logger.Error("Invalid vote name", "name", name)
			return nil
		}
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		logger.Error("Canonicalize error", "name", name, "value", value, "err", err)
		return nil
	}

	if !param.FormatChecker(cv) {
		logger.Error("Format check error", "name", name, "value", value)
		return nil
	}
```

**File:** blockchain/chain_makers.go (L305-307)
```go
	if chain.Config().IsMagmaForkEnabled(header.Number) {
		header.BaseFee = chain.Config().Governance.KIP71.NextMagmaBlockBaseFee(parent.Number(), parent.Header().BaseFee, parent.GasUsed())
	}
```
