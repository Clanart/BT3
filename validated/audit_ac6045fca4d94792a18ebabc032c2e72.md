### Title
Governance-settable `governance.kip71.gastarget = 0` causes division-by-zero panic in `NextMagmaBlockBaseFee` - ([File: params/kip71_config.go])

### Summary
The KIP-71 dynamic base fee governance parameter `Kip71GasTarget` has no lower-bound / non-zero validation, unlike its sibling parameter `Kip71BaseFeeDenominator`, which is explicitly guarded against zero both at the parameter-registration level and defensively inside the calculation function. If `GasTarget` is set to `0` via governance, every subsequent block with `GasUsed > 0` triggers an unguarded `big.Int` division by zero inside `NextMagmaBlockBaseFee`, panicking any node that validates the header or assembles a block — a chain-wide liveness failure. This mirrors the reported bug class: a computation path (analogous to the reward-distribution loop dividing by `currentRoundAgentsAlive`) that is not guarded against a zero denominator that can be driven to zero by an actor with control over the relevant input.

### Finding Description
`Kip71GasTarget`'s governance parameter definition uses `noopFormatChecker`, which accepts any `uint64` value including `0`: [1](#0-0) 

By contrast, `Kip71BaseFeeDenominator` explicitly rejects `0`: [2](#0-1) 

and `NextMagmaBlockBaseFee` even contains an explicit workaround comment "To avoid panic" that falls back to a safe denominator of 64 if `BaseFeeDenominator == 0`: [3](#0-2) 

However, `gasTarget := kc.GasTarget` receives no equivalent protection. It is later used directly as an unchecked divisor in both branches of the fee-adjustment logic: [4](#0-3) 

Specifically:
- When `parentGasUsed > gasTarget` (true for any block with `GasUsed > 0` when `gasTarget == 0`): `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` at line 102 divides by zero.
- Symmetrically, in the `parentGasUsed < gasTarget` branch, the same `x.Div(x, new(big.Int).SetUint64(gasTarget))` at line 120 would divide by zero (though this branch is unreachable once `gasTarget == 0`, since `parentGasUsed` can never be less than `0`).

Go's `big.Int.Div` panics on division by zero, so this is a hard crash, not merely an error return.

`NextMagmaBlockBaseFee` is invoked from consensus-critical, unprivileged-transaction-driven code paths, including header verification (`VerifyMagmaHeader`, called from `blockchain/block_validator.go`) and block/fee-history assembly (`work/worker.go`, `node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`), meaning every node — not just the proposer — computes this value for essentially every Magma-era block once such a governance value is active.

### Impact Explanation
Once `governance.kip71.gastarget` is set to `0` (a value the format checker does not reject), the very next block containing any gas usage (i.e., almost any real block, submitted by ordinary users) causes every full node, validator, and RPC node computing the expected base fee to panic and crash. This is a network-wide denial-of-service / consensus halt: honest nodes can no longer produce or validate blocks, matching the "High" impact class of the original report (loop/computation proceeding despite a to-be-zero denominator, causing DoS via division by zero).

### Likelihood Explanation
Setting the parameter to `0` requires a governance parameter change (via header-vote or GovParam contract depending on `istanbul.policy`), which the task rules explicitly recognize as an in-scope reachable path ("governance parameters"). Given the codebase already demonstrates awareness of this exact bug class for the sibling `BaseFeeDenominator` parameter (explicit `!= 0` check and defensive runtime fallback) but omits it for `GasTarget`, this looks like an unintentional gap rather than a deliberately accepted risk, making it plausible to trigger either accidentally (an admin fat-fingering `0`) or maliciously by a governing/voting party.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (mirroring `Kip71BaseFeeDenominator`), e.g. `v, ok := cv.(uint64); return ok && v != 0`. Additionally, add the same defensive fallback used for `BaseFeeDenominator` inside `NextMagmaBlockBaseFee` (e.g., if `gasTarget == 0`, substitute a safe non-zero default) so that even a governance misconfiguration or historical/legacy chain data cannot crash node processes.

### Proof of Concept
1. Via governance vote (or GovParam contract, depending on `istanbul.policy`), set `governance.kip71.gastarget = 0`. This passes validation because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker` [1](#0-0) .
2. Once the parameter takes effect at block N, submit any ordinary transaction so that block N's `GasUsed > 0`.
3. When computing block N+1's expected base fee, `NextMagmaBlockBaseFee` executes the `parentGasUsed > gasTarget` branch (since `gasTarget == 0`) and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget = 0` [5](#0-4) , causing a runtime panic in every node that validates or builds the header (`blockchain/block_validator.go` via `VerifyMagmaHeader`, `work/worker.go` during block assembly), halting the chain.

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

**File:** params/kip71_config.go (L88-121)
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
```
