### Title
Division by zero in KIP-71 `NextMagmaBlockBaseFee` via unvalidated `GasTarget` governance parameter - (File: `params/kip71_config.go`)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` divides by `kc.GasTarget` (and by the derived `gasTarget` `big.Int`) without any guard against it being zero, mirroring the `Conv2DBackpropFilter` bug class where a caller-controlled divisor is used in a modulus/division without a zero check. Unlike the sibling parameter `Kip71BaseFeeDenominator`, which is explicitly validated to be non-zero, the `Kip71GasTarget` governance parameter has no such validation.

### Finding Description
`NextMagmaBlockBaseFee` computes the next block's base fee using `gasTarget` as a divisor in two branches: [1](#0-0) [2](#0-1) 

Both branches call `new(big.Int).SetUint64(gasTarget)` and then `x.Div(x, ...)`, which is Go's `big.Int.Div` — this panics on division by zero. `gasTarget` comes directly from `kc.GasTarget`, i.e., the governance parameter `Kip71GasTarget`: [3](#0-2) 

Crucially, while the sibling parameter `Kip71BaseFeeDenominator` is protected with an explicit non-zero `FormatChecker`: [4](#0-3) 

the `Kip71GasTarget` parameter uses `noopFormatChecker`, performing no validation at all: [5](#0-4) 

There is a defensive fallback for `BaseFeeDenominator == 0` inside `NextMagmaBlockBaseFee` itself: [6](#0-5) 

but no equivalent fallback exists for `gasTarget == 0`.

### Impact Explanation
`NextMagmaBlockBaseFee` is called during header verification and fee-history/oracle processing to compute the next block's `BaseFee`, e.g. in `VerifyMagmaHeader` and RPC fee-history processing: [7](#0-6) [8](#0-7) 

If `GasTarget` is ever set (or defaults) to `0` while a block's gas usage is nonzero (`parentGasUsed != gasTarget`), every node executing this deterministic logic hits `big.Int.Div` with a zero divisor and panics identically. Because the block-processing/verification path runs on all conforming nodes, this deterministically halts the entire network's block production/verification the moment any transaction causes non-zero gas usage in a block whose next-base-fee must be computed — a full chain halt, which is a severe availability/consensus-safety impact, not a mere resource-exhaustion DoS. This falls squarely within the explicitly in-scope "governance parameters" and "state transition and gas/burn accounting" categories.

### Likelihood Explanation
Likelihood depends on `GasTarget` becoming `0`, which today requires it to be set via the governance parameter mechanism (`Kip71GasTarget`) — that path is gated behind governance/council privileges, not a directly unprivileged single transaction. However, once such a value is (mis)configured — whether via a future governance bug, migration script, misconfigured genesis, or an unenforced governance vote — literally any subsequent ordinary transaction from an unprivileged sender that causes non-zero block gas usage triggers the panic on every node, exactly analogous to how the TensorFlow op's caller-supplied divisor of `0` triggers the crash. The complete absence of a zero-check for `GasTarget` (in contrast to the explicit check present for `BaseFeeDenominator`) shows this is an overlooked validation gap rather than an intentionally-accepted risk.

### Recommendation
Add an explicit non-zero `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` (mirroring the one used for `Kip71BaseFeeDenominator`), and additionally add a defensive fallback inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (analogous to the existing `BaseFeeDenominator == 0` fallback) so that `gasTarget == 0` never reaches the `big.Int.Div` call, regardless of how the value got set (governance vote, genesis config, or programmatic default).

### Proof of Concept
1. Set (via governance parameter vote or a misconfigured genesis) `Kip71GasTarget = 0` while Magma/Kaia fork rules are active, exploiting the lack of a `FormatChecker` (`kaiax/gov/param.go:324-334`) that would otherwise reject it.
2. Any subsequent block with `parentGasUsed != 0` (i.e., literally any block containing at least one ordinary transaction submitted by any unprivileged sender) is processed by `NextMagmaBlockBaseFee`.
3. Since `gasTarget == 0`, `parentGasUsed > gasTarget` is true for any nonzero usage, entering the branch at `params/kip71_config.go:100-103`, which executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`.
4. `big.Int.Div` panics with "division by zero" on every node executing header verification / base-fee computation, halting block production and verification network-wide.

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

**File:** params/kip71_config.go (L77-78)
```go
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee
```

**File:** params/kip71_config.go (L99-103)
```go
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```

**File:** params/kip71_config.go (L117-121)
```go
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

**File:** kaiax/gov/param.go (L310-316)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
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

**File:** node/cn/gasprice/feehistory.go (L108-115)
```go
	if bf.results.baseFee = bf.header.BaseFee; bf.results.baseFee == nil {
		bf.results.baseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
