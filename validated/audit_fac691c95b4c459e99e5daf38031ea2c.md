## Analysis Result

### Title
Governance can set `Kip71GasTarget` to zero, causing a divide-by-zero panic in `KIP71Config.NextMagmaBlockBaseFee` - (File: params/kip71_config.go)

### Summary
The KIP-71 (Magma/Kaia dynamic base fee) base-fee update function `NextMagmaBlockBaseFee` divides `parentBaseFee * gasUsedDelta` by `gasTarget` without any zero-check, while the governance parameter that supplies `gasTarget` (`Kip71GasTarget`) uses a no-op format checker that accepts any `uint64` value, including `0`. This is analogous to CVE-2017-9202, a division-by-zero denial-of-service triggered by an unchecked divisor derived from attacker/operator-controlled input.

### Finding Description
`NextMagmaBlockBaseFee` computes the delta to the base fee using the governance-controlled `GasTarget` as a divisor in two branches (gas used above target and below target): [1](#0-0) [2](#0-1) 

Unlike `BaseFeeDenominator`, which is defensively defaulted to `64` when zero: [3](#0-2) 

`GasTarget` has no equivalent protection — it is used directly as `new(big.Int).SetUint64(gasTarget)` divisor.

Critically, the governance parameter registry defines `Kip71BaseFeeDenominator` with an explicit `v != 0` format checker, but `Kip71GasTarget` is defined with `noopFormatChecker`, which always returns `true` regardless of value: [4](#0-3) 

If governance (via a vote, e.g. `governancenode_addVote`/vote mechanism reflected in headergov) sets `GasTarget` to `0` and this becomes the effective `ParamSet` for a future block, then for any block where `parentGasUsed != 0` (i.e., almost every real block), the code enters either the "above target" or "below target" branch and calls `big.Int.Div(x, new(big.Int).SetUint64(0))`, which panics because Go's `math/big` package panics on division by zero.

This function is invoked on every block header validation/base-fee computation path (`VerifyMagmaHeader` calling `NextMagmaBlockBaseFee`), meaning the panic would be triggered deterministically on all nodes processing the next block after the malicious parameter takes effect, and also in `node/cn/gasprice/feehistory.go`'s `processBlock` for RPC `eth_feeHistory` calls: [5](#0-4) 

### Impact Explanation
A panic in `NextMagmaBlockBaseFee` during header validation or fee-history computation crashes the node process (denial of service) across the entire network simultaneously, since all nodes independently compute the same base fee using the same governance parameter. This can halt block production/consensus network-wide, which is a High severity availability impact — a chain-halting DoS is worse than a single-peer resource-exhaustion issue.

### Likelihood Explanation
Triggering requires the governance mechanism to successfully set `GasTarget = 0`, which is gated by governance voting (per the KIP71 config typically controlled by `GoverningNode`/governance council, not an arbitrary unprivileged actor). This lowers reachability compared to a fully permissionless attacker path, but the prompt's explicit scope permits "governance parameters" as an analog category, and the root defect — using `noopFormatChecker` for `GasTarget` while explicitly zero-checking the structurally similar `BaseFeeDenominator` — indicates a genuine, exploitable input-validation gap in the parameter registry that any governance proposal is able to slip through.

### Recommendation
Add a zero-check to the `Kip71GasTarget` `FormatChecker` in `kaiax/gov/param.go` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), and additionally add a defensive fallback default for `GasTarget == 0` inside `NextMagmaBlockBaseFee` in `params/kip71_config.go`, consistent with the existing defensive handling of `BaseFeeDenominator == 0`.

### Proof of Concept
1. Governance proposes and finalizes a vote setting `kip71.gastarget = 0` for a future block (this passes the current `noopFormatChecker` in `kaiax/gov/param.go`, lines 324-334, unlike `Kip71BaseFeeDenominator` which would reject `0`).
2. Once the new `ParamSet` becomes effective, any subsequent block whose parent had `GasUsed != 0` triggers `NextMagmaBlockBaseFee(parentNum, parentBaseFee, parentGasUsed)`.
3. Since `parentGasUsed != gasTarget (0)`, execution enters the `>` or `<` branch at `params/kip71_config.go` lines 92-128, and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0`.
4. `math/big.Int.Div` panics on division by zero, crashing every node that validates the header or computes fee history for that block — a full network-wide denial of service.

### Citations

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

**File:** params/kip71_config.go (L98-103)
```go
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

**File:** node/cn/gasprice/feehistory.go (L105-115)
```go
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
