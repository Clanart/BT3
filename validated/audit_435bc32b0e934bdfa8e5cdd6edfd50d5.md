### Title
Division-by-zero panic in KIP-71 `NextMagmaBlockBaseFee` via unvalidated `kip71.gastarget` governance parameter - (File: `params/kip71_config.go`)

### Summary
The `kip71.gastarget` governance parameter is registered with a `noopFormatChecker` that accepts any `uint64` value, including `0`, unlike its sibling parameter `kip71.basefeedenominator` which explicitly rejects zero. A `GasTarget` of `0` reaches `KIP71Config.NextMagmaBlockBaseFee`, which performs `big.Int` division by `gasTarget` without a zero check, causing a runtime panic (Go's `math/big` division by zero panics rather than returning an error/NaN like the TensorFlow `ParallelConcat` FPE analog).

### Finding Description
`kaiax/gov/param.go` defines the parameter table used for both header-vote governance and KIP-81 contract governance (`GovParam` contract). Compare the two related base-fee parameters: [1](#0-0) [2](#0-1) 

`Kip71BaseFeeDenominator` explicitly enforces `v != 0`, but `Kip71GasTarget` uses `noopFormatChecker`, which always returns `true`: [3](#0-2) 

This same `Params` map (and therefore the same, permissive `FormatChecker`) is used to validate:
- header-governance votes (`kip71.gastarget`), verified in `VerifyVote`, and
- contract-governance values read from the on-chain `GovParam` contract via `ParseContractCall` → `PartialParamSet.Add`, which is populated by any GC member calling `GovParam.setParam` per KIP-81: [4](#0-3) [5](#0-4) 

Once `GasTarget = 0` is accepted into the effective `ParamSet` (and consequently into `KIP71Config` via `ToKip71Config`/`ChainConfigValue`), it flows into `NextMagmaBlockBaseFee`: [6](#0-5) 

When `parentGasUsed > gasTarget` (true for essentially any nonzero gas usage when `gasTarget == 0`), the function computes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divide by 0 -> panic
```
`big.Int.Div` with a zero divisor panics in Go (`"division by zero"`), unlike Solidity's `SafeMath` (which the repo also vendors and correctly guards against, see `contracts/libs/openzeppelin-contracts-v2/contracts/math/SafeMath.sol` lines 83-90) or a graceful error return. The same unguarded division exists in the decreasing-usage branch as well (line 120).

This function is invoked both when constructing a new block's base fee (`work/worker.go`) and when **verifying** a peer/self-produced header's base fee via `VerifyMagmaHeader`, which is called from `blockchain/block_validator.go`: [7](#0-6) 

Because `VerifyMagmaHeader` is part of consensus block header validation, a panic here is hit by every full node that processes the resulting block header (or even the pending-block-building path on every node once the governance vote activates), not just a single malicious peer.

### Impact Explanation
Any Governance Council (GC) member — a "governance parameter" caller in the allowed scope — can push a single header vote (`kip71.gastarget = 0`) or a single `GovParam.setParam` transaction setting `kip71.gastarget` to `0`. Once the parameter activates at the target block:
- Every node's block-assembly path (`work/worker.go`) that computes the next base fee panics.
- Every node's block-validation path (`blockchain/block_validator.go` → `VerifyMagmaHeader`) panics when validating any subsequent header.
- The RPC `eth_feeHistory` path (`node/cn/gasprice/feehistory.go`) also calls `NextMagmaBlockBaseFee` and would panic for any public RPC caller querying fee history once the bad parameter is active.

This crashes/halts the chain network-wide from a single governance transaction — a consensus-halting denial-of-service reachable through the in-scope "governance parameters" attack surface, directly analogous to the CWE-369 division-by-zero FPE described in the TensorFlow advisory.

### Likelihood Explanation
Likelihood is Medium: it requires a GC member to submit the vote/parameter change (not an arbitrary unprivileged EOA), but no additional validation prevents it — the format checker for `kip71.gastarget` is a no-op, in clear contrast to the sibling parameter `kip71.basefeedenominator`, which strongly suggests this zero-check was simply omitted (rather than intentionally allowed). A single malicious or misconfigured GC vote is sufficient to trigger a chain-wide panic once the parameter activates.

### Recommendation
Add a zero-value rejection to the `Kip71GasTarget` `FormatChecker` in `kaiax/gov/param.go`, mirroring `Kip71BaseFeeDenominator`:
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
Additionally, as defense in depth, add an explicit `gasTarget == 0` guard inside `NextMagmaBlockBaseFee` in `params/kip71_config.go` (similar to the existing `baseFeeDenominator == 0` fallback at lines 71-76) so that a zero value from any code path (e.g., genesis/legacy chain configs, tests) cannot cause a panic.

### Proof of Concept
1. As a GC member, submit a header vote: `headergov.NewVoteData(validator.Addr, "kip71.gastarget", uint64(0))`. `NewVoteData`/`VerifyVote` accept it because `Kip71GasTarget.FormatChecker` is `noopFormatChecker` (no rejection, unlike `kip71.basefeedenominator`).
2. Wait for the vote to activate in the effective `ParamSet` (post-Magma, at the epoch boundary), so `GasTarget = 0`.
3. On the next block where `parentGasUsed > 0` (virtually guaranteed), any node calling `KIP71Config.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` — during block assembly (`work/worker.go`), header verification (`blockchain/block_validator.go` via `VerifyMagmaHeader`), or `eth_feeHistory` RPC — executes `x.Div(x, new(big.Int).SetUint64(0))`, which panics with `"division by zero"`, crashing the node process.

Note: I was not able to fully trace the exact runtime behavior of the panic (e.g., whether it is caught by a top-level recover in `work/worker.go` or `blockchain/block_validator.go`) due to index size limits on some files; a Devin session with full codebase access would be needed to confirm whether any outer `recover()` mitigates the crash before concluding the precise blast radius.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
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

**File:** kaiax/gov/contractgov/impl/getter.go (L83-94)
```go
	ret := ParseContractCall(names, values)

	rules := config.Rules(new(big.Int).SetUint64(blockNum))
	for name := range ret {
		if gov.DeprecatedAt(name, rules) {
			logger.Warn("Ignoring deprecated parameter from contract governance", "name", name, "blockNum", blockNum)
			delete(ret, name)
		}
	}

	return ret, nil
}
```

**File:** kaiax/gov/paramset.go (L209-226)
```go
func (p PartialParamSet) Add(name string, value any) error {
	param, ok := Params[ParamName(name)]
	if !ok {
		return ErrInvalidParamName
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		return err
	}

	if !param.FormatChecker(cv) {
		return ErrInvalidParamValue
	}

	p[ParamName(name)] = cv
	return nil
}
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

**File:** params/kip71_config.go (L77-128)
```go
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

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
