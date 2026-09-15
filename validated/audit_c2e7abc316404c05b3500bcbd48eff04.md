## Analysis

The CVE describes a MySQL Optimizer bug where a high-privileged actor can trigger a **complete DoS (hang or crash)** via a value that isn't validated before being used in an internal calculation. The closest reachable analog in kaia is the **KIP‑71 dynamic base-fee ("Magma") calculator**, which is Kaia's analog of a fee/cost "optimizer" — it recomputes the next block's base fee from governance-controlled parameters, and this computation runs unconditionally on every node for every block.

### Title
Governance-settable `kip71.gastarget = 0` causes a division-by-zero panic in `NextMagmaBlockBaseFee`, halting all nodes - ([File: params/kip71_config.go])

### Summary
`GasTarget` is a KIP‑71 governance parameter that is used as a divisor in `NextMagmaBlockBaseFee`, but unlike the sibling parameter `BaseFeeDenominator`, its format checker never rejects zero. A single ratified governance vote setting `kip71.gastarget` to `0` causes every node — proposer and validators alike — to panic with an integer division-by-zero the next time a block with nonzero gas usage is processed, resulting in a complete, repeatable denial of service across the network.

### Finding Description
`Kip71BaseFeeDenominator` explicitly rejects zero in its `FormatChecker`: [1](#0-0) 

But `Kip71GasTarget` uses `noopFormatChecker`, which accepts any `uint64` value including `0`: [2](#0-1) 

This value flows unvalidated into `params.KIP71Config.GasTarget`, and is used as a divisor in `NextMagmaBlockBaseFee`: [3](#0-2) 

When `gasTarget == 0` and any block has `parentGasUsed > 0` (i.e., essentially every real block), the code takes the "usage above target" branch and executes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor. Go's `math/big.Int.Div` panics when the divisor is zero. This function is invoked both when a proposer prepares a header's `BaseFee` and when every other node validates it via `KIP71Config.VerifyMagmaHeader`: [4](#0-3) 

Since `VerifyMagmaHeader`/`NextMagmaBlockBaseFee` is called from block validation logic that runs on every full node (see `BlockValidator.ValidateHeader`, exercised in tests such as `TestValidateHeader`), the panic is not confined to a single node — it will crash **every node** that processes a block after the malicious `GasTarget=0` value is ratified.

### Impact Explanation
This is a chain-wide, repeatable denial-of-service: once the parameter is ratified at an epoch boundary, all full nodes (proposers and validators) panic on the very next block that has nonzero gas usage, which is normal chain operation. This matches the CVE's "hang or frequently repeatable crash (complete DOS)" impact, mapped onto Kaia's governance-parameter and KIP-71 base-fee "optimizer" pathway explicitly in scope per the analog validation rules.

### Likelihood Explanation
Exploitation requires a governing-council member (or, pre-Permissionless-fork, any voting-capable proposer) to cast a single `governance_vote` for `kip71.gastarget = 0`, which passes format validation because `noopFormatChecker` performs no bounds check. This mirrors the CVSS `PR:H` requirement of the source CVE (a high-privileged but still "normal" network-reachable actor, not a malicious peer/validator-only consensus-message attack). No code execution or contract deployment is needed — a single vote transaction is sufficient once ratified.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0` (and any value that could make `NextMagmaBlockBaseFee`'s divisions degenerate), consistent with the existing check on `Kip71BaseFeeDenominator`. Additionally, harden `NextMagmaBlockBaseFee` itself to defensively guard against a zero `gasTarget` (similar to the existing zero-guard already present for `baseFeeDenominator`), so that a corrupted or legacy config cannot crash block processing.

### Proof of Concept
1. As a governing-council voter, submit `governance_vote("kip71.gastarget", 0)`. `NewVoteData`/`FormatChecker` accepts it because `Kip71GasTarget` uses `noopFormatChecker`.
2. Once ratified at the next epoch boundary, `ParamSet.GasTarget` becomes `0` for subsequent blocks.
3. On the next block with `GasUsed > 0`, both the block proposer (header preparation) and every validating node (header verification) call `NextMagmaBlockBaseFee`, hit the `parentGasUsed > gasTarget` branch, and execute `big.Int.Div(x, big.NewInt(0))`, which panics — crashing every node that processes the block.

### Citations

**File:** kaiax/gov/param.go (L310-315)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
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

**File:** params/kip71_config.go (L88-109)
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
```
