### Title
Governance-settable `kip71.gastarget = 0` causes a division-by-zero panic in `NextMagmaBlockBaseFee`, permanently halting block validation and block assembly - (File: params/kip71_config.go)

### Summary
The KIP-71 dynamic base fee parameter `GasTarget` is fully governance-settable via a header-based vote, but its format checker never rejects zero. `KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` whenever `parentGasUsed != gasTarget`, so once `GasTarget` becomes `0` and any block thereafter uses any gas, every node that calls this function (header verification, tx-pool gas price estimation, block sealing, RPC fee-history) panics on division by zero. Since this function is invoked deterministically by all nodes for every block after the Magma fork, this can halt the entire network — the same "wrongly parameterized system → division by zero → system unusable" bug class described in the report, but relocated to Kaia's KIP-71 base-fee/governance-parameter subsystem.

### Finding Description
`KIP71Config.GasTarget` is defined in `params/kip71_config.go` and used unguarded in `NextMagmaBlockBaseFee`: [1](#0-0) [2](#0-1) [3](#0-2) 

Both the "gas used above target" and "gas used below target" branches perform `x.Div(x, new(big.Int).SetUint64(gasTarget))`. `big.Int.Div` panics with a division-by-zero error if the divisor is zero. The only branch that avoids the division is `parentGasUsed == gasTarget`, which is only safe as long as `gasTarget` also equals `parentGasUsed`; as soon as any subsequent block has nonzero gas usage, the divisor-zero branches are hit.

Unlike `Kip71BaseFeeDenominator`, which explicitly rejects zero: [4](#0-3) 

`Kip71GasTarget`'s format checker is `noopFormatChecker`, meaning **any** uint64 value, including `0`, passes validation and can be voted in and committed to the chain governance state: [5](#0-4) 

This `FormatChecker` is exactly the gate used to validate votes before they are accepted, as seen in `kaiax/gov/headergov/vote.go`'s use of `Params[...].FormatChecker` and in the test matrix that explicitly documents `Kip71GasTarget` accepting values without a positivity constraint (contrast with `Kip71BaseFeeDenominator`, which is tested to reject `0`): [6](#0-5) [7](#0-6) 

Line 139-140 of the bad-votes table shows `Kip71GasTarget` rejects only non-uint64 types (string, int, bool), never rejecting the value `0` — confirming zero is an accepted, valid vote value for this parameter.

`NextMagmaBlockBaseFee`/`VerifyMagmaHeader` are called from consensus-critical, deterministic code paths reachable by ordinary block processing, including `blockchain/block_validator.go` (header verification for every incoming block) and `work/worker.go` (block assembly by the block proposer), plus RPC-facing code such as `node/cn/gasprice/gasprice.go` and `node/cn/gasprice/feehistory.go`. Because these are called by every node (not just the governing node), a single erroneous governance vote setting `GasTarget = 0` will eventually crash all full nodes and validators simultaneously once a block with nonzero gas usage is produced under that parameter — a full network halt.

### Impact Explanation
This is analogous to the reported Sai bug class ("wrongly parameterized system leads to division by zero, blocking the system"), but mapped onto Kaia's governance parameter subsystem for KIP-71 dynamic base fee calculation. The impact is severe: it is not merely "temporarily blocked" for one contract, but causes a **deterministic panic in block header verification and block sealing across the entire network**, i.e., a chain-wide denial of service / consensus halt. All nodes (validators, full nodes, RPC nodes, tx-pool logic) that process a block after `GasTarget` is set to `0` and gas is used in a subsequent block will panic, since the function is called unconditionally for every block post-Magma fork.

### Likelihood Explanation
Likelihood depends on human/organizational error in the governance voting process (e.g., an operator submitting `kip71.gastarget=0` unintentionally, similar to the "short address issue" scenario in the original report, or a malicious/compromised governing node voting it in). This requires no unprivileged transaction — it requires a governance vote to be committed by the governing node(s), which is explicitly one of the allowed analog categories ("governance parameters"). Because the validation layer (`FormatChecker`) is the single safety net supposed to catch such malformed parameter values before they are ever written to chain state, and it fails to do so for `GasTarget`, the likelihood of this class of failure is non-trivial whenever a governance mistake occurs — there is no other defense-in-depth check anywhere in `NextMagmaBlockBaseFee` itself.

### Recommendation
- Add a positivity constraint to `Kip71GasTarget`'s `FormatChecker` in `kaiax/gov/param.go`, mirroring the existing `Kip71BaseFeeDenominator` check (`ok && v != 0`), to reject a governance vote/parameter value of `0`.
- Defensively guard `NextMagmaBlockBaseFee` in `params/kip71_config.go` against `gasTarget == 0` (e.g., fall back to a safe default the same way `BaseFeeDenominator == 0` is already handled), so that even a value that somehow bypasses the format checker (e.g., via genesis misconfiguration) cannot cause a runtime panic.
- Audit all other KIP-71/governance numeric parameters that are used as divisors (e.g., any future denominators) to ensure all format checkers explicitly reject zero, following the general recommendation that "all functions assigning parameters ... should check that the parameters have reasonable values."

### Proof of Concept
1. Governing node submits and gets a governance vote for `kip71.gastarget = 0` accepted (passes `noopFormatChecker`, is written into governance state at the next epoch block) — confirmed acceptance path in `kaiax/gov/headergov/vote_test.go` (`Kip71GasTarget` only rejects non-uint64 types, not zero).
2. At the next epoch, the new `ParamSet`/`KIP71Config` for subsequent blocks has `GasTarget = 0`.
3. A block is produced/verified with `parentHeaderGasUsed > 0` (virtually guaranteed under real traffic). `NextMagmaBlockBaseFee` computes `parentGasUsed > gasTarget` (since `gasTarget == 0`), enters the branch performing `x.Div(x, new(big.Int).SetUint64(gasTarget))` with `gasTarget == 0`.
4. `big.Int.Div` panics with "division by zero" inside `params/kip71_config.go` line ~102 (or ~121 in the below-target branch), which is invoked from `VerifyMagmaHeader` (header verification, called by `blockchain/block_validator.go`) and from block sealing (`work/worker.go`), crashing the node process on every node that reaches this code path — halting the chain.

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

**File:** params/kip71_config.go (L88-102)
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

**File:** kaiax/gov/headergov/vote_test.go (L35-40)
```go
		{name: gov.Kip71BaseFeeDenominator, value: uint64(64)},
		{name: gov.Kip71GasTarget, value: uint64(15000000)},
		{name: gov.Kip71GasTarget, value: uint64(30000000)},
		{name: gov.Kip71LowerBoundBaseFee, value: uint64(25000000000)},
		{name: gov.Kip71MaxBlockGasUsedForBaseFee, value: uint64(84000000)},
		{name: gov.Kip71UpperBoundBaseFee, value: uint64(750000000000)},
```

**File:** kaiax/gov/headergov/vote_test.go (L133-140)
```go
		{name: gov.Kip71BaseFeeDenominator, value: "64"},
		{name: gov.Kip71BaseFeeDenominator, value: "sixtyfour"},
		{name: gov.Kip71BaseFeeDenominator, value: 64},
		{name: gov.Kip71BaseFeeDenominator, value: false},
		{name: gov.Kip71GasTarget, value: "30000"},
		{name: gov.Kip71GasTarget, value: 3000},
		{name: gov.Kip71GasTarget, value: false},
		{name: gov.Kip71GasTarget, value: true},
```
