### Title
Governance vote validation for KIP-71 base-fee bounds checks against stale ParamSet, allowing `lowerBoundBaseFee > upperBoundBaseFee` to be committed - (File: kaiax/gov/headergov/impl/header.go)

### Summary
`checkConsistency` in `kaiax/gov/headergov/impl/header.go` validates a `Kip71LowerBoundBaseFee` or `Kip71UpperBoundBaseFee` vote only against the currently *active* `ParamSet` (i.e., the parameter values from before the current epoch), not against any other pending vote cast within the same epoch. Because both bounds can be voted independently within one epoch and are merged and applied together at the epoch boundary, it is possible to commit a final state where `lowerBoundBaseFee > upperBoundBaseFee`, mirroring the reported `vestingStart`/`vestingEnd` inversion bug (each setter validates against a stale reference instead of the resulting combined state).

### Finding Description
The vote-time consistency check is: [1](#0-0) 

Both branches call `h.GetParamSet(blockNum)`, which reflects governance state as of the *start* of the current epoch (see `getExpectedGovernance`, which aggregates votes per previous epoch and only takes effect at the next epoch boundary): [2](#0-1) 

Because each vote is checked independently against the pre-epoch `ParamSet` rather than against the other bound's pending (not-yet-applied) vote within the same epoch, a proposer can cast, in successive blocks of the same epoch:
1. A vote to raise `lowerBoundBaseFee` to a large value `L2` — validated against the current (still small) `upperBoundBaseFee`, which passes.
2. A vote to lower `upperBoundBaseFee` to a small value `U2` — validated against the current (still small) `lowerBoundBaseFee`, which also passes.

At the epoch boundary both votes are merged into the same `Governance` header field via `getExpectedGovernance`/`VerifyGov`, with no cross-check that the resulting combined `ParamSet` still satisfies `lowerBoundBaseFee <= upperBoundBaseFee`: [3](#0-2) 

The resulting inverted bounds are then fed unchecked into `NextMagmaBlockBaseFee`, which clamps `parentBaseFee` against `upperBoundBaseFee`/`lowerBoundBaseFee` and computes fee deltas assuming `lower <= upper`: [4](#0-3) 

With `lowerBoundBaseFee > upperBoundBaseFee`, the clamping logic can produce nonsensical, oscillating results — e.g., a block whose gas usage is below target (which should decrease the base fee) can instead jump the fee up to the (now higher) `lowerBoundBaseFee`, and vice versa — breaking the intended monotonic KIP-71 pricing mechanism permanently until governance corrects it.

### Impact Explanation
This corrupts the base-fee (KIP-71) dynamic pricing mechanism network-wide: base fee calculations become inconsistent with actual gas usage, potentially forcing the fee to oscillate between the two inverted bounds regardless of demand. This can cause legitimately-priced transactions to be rejected as underpriced, or conversely force artificially inflated fees, functioning as a chain-wide griefing/DoS on transaction admission — directly analogous to the original "grief on transfers" impact once an inconsistent time/threshold pair is committed to state without a final ordering check. Because both branches of `checkConsistency` validate independently against a stale prior-epoch value instead of the co-pending vote, this is a genuine validation gap (not merely admin trust), exactly matching the judged root cause in the original report ("inconsistency in setters ... can cause an issue with the contract, not merely an Admin Privilege").

### Likelihood Explanation
The GovernanceMode="single" scenario limits voting to the governing node, which is a governance/administrative actor already permitted for analysis per the task scope ("governance parameters" is an explicitly allowed category). No malicious peer/validator collusion is required — a single governing-node voter issuing two separate correctly-formatted votes within one epoch (a completely normal governance workflow of adjusting both bounds) is sufficient to trigger the bug; there is no requirement for cross-vote intent to be malicious, only for the two updates to be issued in the same epoch without careful sequencing, exactly like the original finding's acknowledged "process to edit ... would be to first edit X".

### Recommendation
When validating `Kip71LowerBoundBaseFee` / `Kip71UpperBoundBaseFee` votes in `checkConsistency`, incorporate any other pending vote for the paired bound already accepted within the current epoch (not just the previously finalized `ParamSet`) before accepting the new vote. Additionally, `VerifyGov`/`getExpectedGovernance` should perform a final sanity check on the fully merged `ParamSet` for an epoch (i.e., assert `LowerBoundBaseFee <= UpperBoundBaseFee`) before it is accepted as valid governance for that epoch, rejecting the header otherwise.

### Proof of Concept
1. Assume default state: `lowerBoundBaseFee = 25_000_000_000`, `upperBoundBaseFee = 750_000_000_000`.
2. In epoch N, the governing node (as block proposer) casts a vote at block `b1` setting `Kip71LowerBoundBaseFee = 500_000_000_000`. `checkConsistency` compares `500e9 > params.UpperBoundBaseFee (750e9)`? false → vote accepted [5](#0-4) .
3. Later in the same epoch, at block `b2`, the same node casts a vote setting `Kip71UpperBoundBaseFee = 400_000_000_000`. `checkConsistency` compares `400e9 < params.LowerBoundBaseFee (25e9, still the pre-epoch value)`? false → vote accepted [6](#0-5) .
4. At the epoch boundary, `getExpectedGovernance` merges both votes into the same `Governance` payload with no cross-field check [2](#0-1) , producing an effective `ParamSet` with `LowerBoundBaseFee (500e9) > UpperBoundBaseFee (400e9)`.
5. From the next epoch on, `NextMagmaBlockBaseFee` uses these inverted bounds for every block's base-fee computation, producing incorrect/oscillating fee results as described above [7](#0-6) .

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L112-154)
```go
// VerifyGov checks the following:
// (1) governance must be empty in non-epoch block,
// (2) if there are no votes in the previous epoch, governance must be empty,
// (3) if any vote exists in the previous epoch, governance must not be empty,
// (4) the json must not contain unknown fields,
// (5) the parsed json must exactly match the map derived locally from the previous epoch's votes.
func (h *headerGovModule) VerifyGov(header *types.Header) error {
	// (1)
	if header.Number.Uint64()%h.epoch != 0 {
		if len(header.Governance) > 0 {
			logger.Error("governance is not allowed in non-epoch block", "num", header.Number.Uint64())
			return ErrGovInNonEpochBlock
		} else {
			return nil
		}
	}

	// (2), (3)
	expected := h.getExpectedGovernance(header.Number.Uint64())
	if len(header.Governance) == 0 {
		if len(expected.Items()) != 0 {
			return ErrGovVerification
		}

		return nil
	}

	// (4)
	var gb headergov.GovBytes = header.Governance
	actual, err := gb.ToGovData()
	if err != nil {
		logger.Error("DeserializeHeaderGov error", "num", header.Number.Uint64(), "governance", gb, "err", err)
		return err
	}

	// (5)
	if !reflect.DeepEqual(expected, actual) {
		logger.Error("Governance mismatch", "expected", expected, "actual", actual)
		return ErrGovVerification
	}

	return nil
}
```

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
