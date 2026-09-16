### Title
Missing cross-vote consistency check allows `kip71.lowerboundbasefee` to exceed `kip71.upperboundbasefee` within a single epoch, corrupting KIP-71 base-fee computation - (File: `kaiax/gov/headergov/impl/header.go`)

### Summary
Analogous to the reported `IchiVaultSpell.addCollateralsSupport` bug — where a bounded parameter (`maxLTV`) can be individually set to a value that violates an invariant relied upon elsewhere (`maxLTV <= 1e4`) — Kaia's header-governance vote validation for `kip71.lowerboundbasefee` / `kip71.upperboundbasefee` only checks each vote against the *currently effective* (already-committed) parameter set, not against other votes accumulated in the same still-pending epoch. Two individually "valid" votes can combine into a final governance state where `LowerBoundBaseFee > UpperBoundBaseFee`, which is an invariant that `KIP71Config.NextMagmaBlockBaseFee` assumes always holds.

### Finding Description
`checkConsistency` in [1](#0-0)  validates a `Kip71LowerBoundBaseFee` vote against `h.GetParamSet(blockNum).UpperBoundBaseFee`, and a `Kip71UpperBoundBaseFee` vote against `h.GetParamSet(blockNum).LowerBoundBaseFee`. `GetParamSet(blockNum)` returns the parameter set effective at that block — i.e., the *last committed* governance state — not the state including any other vote already cast earlier in the same epoch (votes are only aggregated and committed together at the epoch boundary via `getExpectedGovernance`, see [2](#0-1) ).

Consequently, within one epoch:
1. A validator casts a vote raising `Kip71LowerBoundBaseFee` to a value just below the *current* (old) `UpperBoundBaseFee` → passes the check.
2. A different validator (proposer of a later block in the same epoch) casts a vote lowering `Kip71UpperBoundBaseFee` to a value just above the *current* (old, still-lower) `LowerBoundBaseFee` → also passes the check, because it is validated against the stale, not-yet-updated `LowerBoundBaseFee`.

Both votes pass individually, but when merged into the next epoch's governance set the invariant `LowerBoundBaseFee <= UpperBoundBaseFee` is broken. Individual `Param.FormatChecker`s for these fields are no-ops ( [3](#0-2) ), so nothing else re-validates the combined result before it is committed to `header.Governance`.

This directly breaks the invariant assumed by `KIP71Config.NextMagmaBlockBaseFee`, [4](#0-3) , which clamps `parentBaseFee`/`nextBaseFee` against `lowerBoundBaseFee` and `upperBoundBaseFee` independently. When `lowerBoundBaseFee > upperBoundBaseFee`, the decreasing-gas-usage branch can clamp and return `lowerBoundBaseFee`, which is strictly greater than `upperBoundBaseFee`, silently violating the documented upper-bound guarantee of KIP-71 base fee.

### Impact Explanation
The next block's base fee can be forced to an out-of-range (excessively high, since `lowerBoundBaseFee` was voted upward) value that violates the protocol's own upper-bound guarantee. Because base fee directly determines the minimum gas price required for transaction inclusion, this can spike required fees network-wide, effectively pricing out ordinary transactions/pausing normal activity, and represents acceptance of a base-fee value that violates a core protocol invariant guaranteed by governance parameters — analogous to the "incorrect behaviors" impact described in the original report (system operating outside intended bounds due to an unchecked combination of otherwise-valid inputs).

### Likelihood Explanation
Requires two council/validator members (or the same validator across two blocks) to cast conflicting `kip71.lowerboundbasefee` / `kip71.upperboundbasefee` votes within the same epoch — a normal governance action reachable through the standard header-vote mechanism, not requiring any node compromise, p2p manipulation, or off-protocol access. In non-`single` governance mode this requires no special permission beyond being a council member proposing a block, which is a legitimate expected voting flow rather than an attack requiring privileged/administrative bypass.

### Recommendation
`checkConsistency` should validate new `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` votes against the *pending* combined value (including other votes already accepted earlier in the same epoch), not just the last-committed `ParamSet`. Alternatively, `getExpectedGovernance`/`VerifyGov` should perform a final consistency pass over the fully merged epoch governance set (asserting `LowerBoundBaseFee <= UpperBoundBaseFee`) before it is accepted into `header.Governance`.

### Proof of Concept
1. Start an epoch with default `LowerBoundBaseFee = 25e9`, `UpperBoundBaseFee = 750e9`.
2. Validator A proposes a block with vote `kip71.lowerboundbasefee = 700e9` (checked against current Upper `750e9` → `700e9 <= 750e9`, passes; see `checkConsistency` case `Kip71LowerBoundBaseFee`).
3. Validator B proposes a later block in the same epoch with vote `kip71.upperboundbasefee = 100e9` (checked against current Lower `25e9` → `100e9 >= 25e9`, passes; case `Kip71UpperBoundBaseFee`, unaware of A's still-pending vote).
4. At epoch boundary, `getExpectedGovernance` merges both votes into `Governance = {Lower: 700e9, Upper: 100e9}`, which is committed with no further consistency check.
5. From the next epoch onward, `NextMagmaBlockBaseFee` operates with `LowerBoundBaseFee(700e9) > UpperBoundBaseFee(100e9)`, and can return `700e9` — a base fee violating the (nominally lower) upper bound of `100e9`.

### Citations

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

**File:** kaiax/gov/headergov/impl/header.go (L226-242)
```go
// The blockNum's epoch index must be greater than 0. That is, it must be blockNum >= epoch.
func (h *headerGovModule) getExpectedGovernance(blockNum uint64) headergov.GovData {
	prevEpochIdx := calcEpochIdx(blockNum, h.epoch) - 1
	prevEpochVotes := h.getVotesInEpoch(prevEpochIdx)
	govs := make(gov.PartialParamSet)

	sortedVoteBlocks := slices.Collect(maps.Keys(prevEpochVotes))
	slices.Sort(sortedVoteBlocks)

	for _, voteBlock := range sortedVoteBlocks {
		vote := prevEpochVotes[voteBlock]
		govs.Add(string(vote.Name()), vote.Value())
	}

	// assert(len(headergov.NewGovData(govs).Items()) == len(govs))
	return headergov.NewGovData(govs)
}
```

**File:** kaiax/gov/param.go (L335-367)
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
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
```

**File:** params/kip71_config.go (L58-129)
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

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
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
}
```
