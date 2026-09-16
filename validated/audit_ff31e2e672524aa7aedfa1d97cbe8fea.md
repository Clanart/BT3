### Title
Division-by-zero panic in KIP-71 (Magma) base fee computation via unchecked `GasTarget` governance parameter - (File: params/kip71_config.go)

### Summary
`KIP71Config.NextMagmaBlockBaseFee` in `params/kip71_config.go` divides by `kc.GasTarget` without ever checking it for zero, unlike the sibling parameter `BaseFeeDenominator`, which is explicitly protected. The governance parameter registry in `kaiax/gov/param.go` defines `Kip71GasTarget` with `FormatChecker: noopFormatChecker`, meaning a governance vote can set `GasTarget = 0` and the value passes validation unmodified. Once active, every subsequent Magma-era header verification and every `eth_feeHistory` RPC call for the corresponding block computes `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`, which panics in Go's `math/big` (the Go analog of the floating-point/integer division exception described in the TensorFlow `AudioSpectrogram` advisory, where an unchecked `stride`/divisor value reaches a division operation).

### Finding Description
`params/kip71_config.go` computes the next Magma base fee as follows: [1](#0-0) 

Notice `BaseFeeDenominator` is explicitly guarded against zero. `GasTarget`, however, is used unguarded a few lines later: [2](#0-1) 

Both the "gas used above target" branch and the "gas used below target" branch execute `x.Div(x, new(big.Int).SetUint64(gasTarget))`. If `gasTarget == 0`, `big.Int.Div` panics with "division by zero" — the Go equivalent of the CWE-697/CWE-369 floating point/integer division exception in the referenced advisory, where an attacker-controlled divisor (`stride`) reached a division operation without a zero-check.

The root cause of reachability is in the governance parameter table, `kaiax/gov/param.go`: [3](#0-2) 

`Kip71BaseFeeDenominator` has `FormatChecker: func(cv any) bool { v, ok := cv.(uint64); return ok && v != 0 }`, but `Kip71GasTarget` uses `FormatChecker: noopFormatChecker`, which imposes no constraint at all. This asymmetry shows the zero-check was deliberately added for one divisor but omitted for the other — the exact bug class described in the report (an attacker-influenced divisor reaching a division operation with no validation).

This unguarded value flows into consensus-critical header validation: [4](#0-3) 
`VerifyMagmaHeader` calls `NextMagmaBlockBaseFee` and is invoked from `blockchain/block_validator.go` during block header verification — meaning every full node computes this per Magma-era block.

It is also reachable from a plain public RPC call, `eth_feeHistory` / `kaia_feeHistory`, via `node/cn/gasprice/feehistory.go`: [5](#0-4) 
`oracle.processBlock` fetches the `ParamSet` for the queried block and calls `kip71Config.NextMagmaBlockBaseFee(...)` directly, with no additional guard.

### Impact Explanation
Once `GasTarget` is set to `0` for a given block range (via a governance vote, which is an explicitly in-scope surface per this analysis, alongside "governance parameters"), the panic triggers in two independently dangerous ways:
1. **Consensus/liveness failure**: Every full node that verifies a Magma-fork header for the affected block range calls `VerifyMagmaHeader` → `NextMagmaBlockBaseFee`, causing a crash/panic in the block-processing goroutine on all honest nodes simultaneously — a chain-halting denial of service.
2. **Public RPC crash**: Any unauthenticated RPC caller invoking `eth_feeHistory` for a block in the affected range triggers the same panic inside the gas price oracle, crashing the RPC handler goroutine (and potentially the node process, depending on panic recovery middleware).

This satisfies the "Medium/High/Critical" and "acceptance of invalid transaction or block / state divergence" criteria in the rules — a chain-wide DoS from a governance-parameter misconfiguration that passes format validation undetected.

### Likelihood Explanation
Likelihood depends on a governance vote successfully setting `Kip71GasTarget` to `0`. Because the parameter's `FormatChecker` is `noopFormatChecker` (always accepts), there is no validation-layer defense preventing this value from being accepted and activated, unlike the sibling `BaseFeeDenominator` parameter which is explicitly hardened. Any process that can submit a `Kip71GasTarget=0` governance vote (or any bug/misconfiguration that leads to it) will deterministically crash the network at the next affected block, and even without a full governance vote, a single `eth_feeHistory` RPC query against a block already configured with `GasTarget=0` is enough to crash that node's RPC handler.

### Recommendation
Add a zero-check to the `Kip71GasTarget` `FormatChecker` in `kaiax/gov/param.go`, mirroring the existing `Kip71BaseFeeDenominator` check (`v != 0`), and additionally add a defensive `if gasTarget == 0 { ... }` fallback inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (as is already done for `BaseFeeDenominator == 0`), so that even a mis-set config file/state cannot trigger a division-by-zero panic.

### Proof of Concept
1. Submit/approve a governance vote setting `Kip71GasTarget = 0` (accepted because `FormatChecker` is `noopFormatChecker`, with no zero-rejection).
2. Once active, at the next Magma-fork block where `parentGasUsed != 0`:
   - Every node validating the header calls `params.KIP71Config.VerifyMagmaHeader` → `NextMagmaBlockBaseFee`, executing `gasUsedDelta := parentGasUsed - 0`, `x := parentBaseFee * gasUsedDelta`, then `y := x.Div(x, big.NewInt(0))`, which panics.
   - Alternatively, without waiting for a new block, calling `eth_feeHistory` (or `kaia_feeHistory`) via public RPC for a block using this `GasTarget=0` param set immediately triggers `oracle.processBlock` → `kip71Config.NextMagmaBlockBaseFee(...)` → the same panic, crashing the RPC-serving goroutine.

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

**File:** params/kip71_config.go (L98-121)
```go
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

**File:** node/cn/gasprice/feehistory.go (L99-115)
```go
func (oracle *Oracle) processBlock(bf *blockFees, percentiles []float64) {
	var (
		chainconfig      = oracle.backend.ChainConfig()
		isCurrBlockMagma = chainconfig.IsMagmaForkEnabled(big.NewInt(int64(bf.blockNumber)))
		isNextBlockMagma = chainconfig.IsMagmaForkEnabled(big.NewInt(int64(bf.blockNumber + 1)))

		pset        = oracle.govModule.GetParamSet(bf.blockNumber + 1)
		kip71Config = pset.ToKip71Config()
	)
	if bf.results.baseFee = bf.header.BaseFee; bf.results.baseFee == nil {
		bf.results.baseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
	if isNextBlockMagma {
		bf.results.nextBaseFee = kip71Config.NextMagmaBlockBaseFee(bf.header.Number, bf.header.BaseFee, bf.header.GasUsed)
	} else {
		bf.results.nextBaseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
```
