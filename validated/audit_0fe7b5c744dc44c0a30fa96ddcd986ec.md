Confirmed: `Kip71GasTarget` uses `noopFormatChecker` (accepts any `uint64`, including `0`), while `Kip71BaseFeeDenominator` explicitly rejects `0` right next to it in the same file [1](#0-0) . `gasTarget` is later used unguarded as a divisor in `NextMagmaBlockBaseFee` [2](#0-1) .

### Title
Governance-settable `kip71.gastarget` parameter lacks a nonzero/lower-bound constraint, allowing a division-by-zero panic in base fee computation - (File: params/kip71_config.go)

### Summary
The `Kip71GasTarget` governance parameter is registered with `noopFormatChecker`, meaning any `uint64` value, including `0`, is accepted as a valid vote value. This value is later used directly as a divisor inside `KIP71Config.NextMagmaBlockBaseFee`, the function that computes every block's base fee under the Magma/KIP-71 dynamic gas pricing rule. If `GasTarget` is set to `0`, the very next block with nonzero gas usage triggers a `big.Int` division by zero, which panics. This mirrors the referenced bug class (an admin/governance-controlled numeric parameter with no upper/lower bound or sanity constraint), but here the unconstrained value is directly consumed as an arithmetic divisor in the mandatory per-block state-transition path, making the impact a full network halt rather than an economic imbalance.

### Finding Description
`kaiax/gov/param.go` defines the governance parameter table `Params`. Most numeric governance parameters use a `FormatChecker` to reject unsafe values — for example `Kip71BaseFeeDenominator` explicitly requires `v != 0` [3](#0-2) . In contrast, `Kip71GasTarget` uses `noopFormatChecker`, which always returns `true` regardless of the value [4](#0-3)  and [5](#0-4) .

This canonical/checked value flows into `ParamSet.GasTarget` [6](#0-5)  and then into `KIP71Config.GasTarget`, which is consumed by `NextMagmaBlockBaseFee`:
- `gasTarget := kc.GasTarget` is taken from the (unchecked) governance value [7](#0-6) .
- Unlike `BaseFeeDenominator`, which has an explicit `== 0` guard with a fallback value ("To avoid panic, set the fluctuation range small") [8](#0-7) , `gasTarget` has no equivalent guard.
- When the parent block's gas usage is nonzero (`parentGasUsed > gasTarget` becomes true for `gasTarget == 0` and any `parentGasUsed > 0`), the code computes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` [9](#0-8) , dividing by the zero-valued `gasTarget`. Go's `big.Int.Div` panics on division by zero.
- The symmetric decrease branch contains the identical unguarded division `y := x.Div(x, new(big.Int).SetUint64(gasTarget))` [10](#0-9) , though that branch is unreachable once `gasTarget == 0` since `parentGasUsed` (a `uint64`) can never be less than `0`.

`NextMagmaBlockBaseFee` is invoked both when a validator prepares a new block (computing the block's own `BaseFee`) and when any node validates a received block's header via `VerifyMagmaHeader`, which calls the same function to recompute the expected base fee [11](#0-10) . This means the panic path is reached deterministically by every full node in the network, not just the party who cast the vote.

### Impact Explanation
Once a `kip71.gastarget = 0` governance vote is cast, tallied, and activated at a target block, all nodes (validators processing the next block and full nodes verifying headers) will invoke `NextMagmaBlockBaseFee`. As soon as a block with nonzero gas usage is proposed at or after the activation height, the division-by-zero panic crashes the node process building/verifying that block. Because this computation is deterministic and consensus-critical, it affects every conforming node simultaneously, producing a chain-wide halt (denial of service) rather than an isolated single-node crash. This is a more severe consequence than the "overcapitalization/imbalance" impact described in the original report, since it results in acceptance-path failure (crash) for the entire network rather than merely skewed economics — directly satisfying the "acceptance of an invalid transaction or block"/availability class of impact for the ecosystem.

### Likelihood Explanation
Setting `kip71.gastarget` requires a governance vote, which is restricted to the governing node/validator set (analogous to the "admin" role in the original finding — a privileged but legitimate protocol actor, not a malicious external party). No additional protocol-level safeguard rejects `0` for this specific parameter, unlike its sibling parameter `Kip71BaseFeeDenominator`, which received an explicit fix for the same class of issue. Given the trivial precondition (a single governance vote setting one integer to `0`) and the deterministic, unavoidable trigger (any subsequent block with `gasUsed > 0`), likelihood is high once such a vote is approved.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`/`LowerBoundBaseFee`/`UpperBoundBaseFee` for consistency, e.g., enforcing `LowerBoundBaseFee <= UpperBoundBaseFee`) that rejects `v == 0`, matching the existing pattern used for `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check with a safe fallback directly inside `NextMagmaBlockBaseFee` (mirroring the existing `BaseFeeDenominator == 0` fallback) so that a division by zero can never occur regardless of upstream validation gaps.

### Proof of Concept
1. A governing-node/validator vote sets `kip71.gastarget` to `0` (accepted because `Kip71GasTarget`'s `FormatChecker` is `noopFormatChecker`, which returns `true` unconditionally) [4](#0-3) .
2. The vote is tallied and the new `ParamSet.GasTarget = 0` becomes active at some block height `N`.
3. At block `N+1`, a block with `gasUsed > 0` is proposed (trivial — any ordinary transaction).
4. Every node computing the expected base fee calls `KIP71Config.NextMagmaBlockBaseFee(parentNumber, parentBaseFee, parentGasUsed>0)`; since `gasTarget == 0`, execution enters the `parentGasUsed > gasTarget` branch and executes `x.Div(x, new(big.Int).SetUint64(0))` [12](#0-11) , which panics with "division by zero" in every node (both the block proposer computing `BaseFee` for its own header, and all peers validating the header via `VerifyMagmaHeader`) [11](#0-10) .
5. Result: network-wide node crash / chain halt at block `N+1`.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
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

**File:** params/kip71_config.go (L77-102)
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
```

**File:** params/kip71_config.go (L118-120)
```go
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
```

**File:** kaiax/gov/paramset.go (L77-78)
```go
	case Kip71GasTarget:
		p.GasTarget, ok = cv.(uint64)
```
