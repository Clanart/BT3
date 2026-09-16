### Title
KIP-71 lower/upper base fee bound consistency check validates against stale pre-epoch state, allowing votes to combine into an inverted `LowerBoundBaseFee > UpperBoundBaseFee` governance state - (File: `kaiax/gov/headergov/impl/header.go`)

### Summary
The Sherlock report shows that `mintPriceThreshold` and `redeemPriceThreshold` are set independently without validating their relative ordering, letting the admin (or two independent setter calls) reach an economically invalid combined state. Kaia's KIP-71 base-fee governance has the structurally identical flaw: `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` votes are each checked for consistency against the *other bound's currently-active (pre-epoch) value*, not against each other's newly proposed values or the final combined state that will actually be committed at epoch end.

### Finding Description
`checkConsistency` in `kaiax/gov/headergov/impl/header.go` validates a `Kip71LowerBoundBaseFee` vote by comparing it to `params.UpperBoundBaseFee`, and a `Kip71UpperBoundBaseFee` vote by comparing it to `params.LowerBoundBaseFee`, where `params := h.GetParamSet(blockNum)` [1](#0-0)  is the currently *committed* ParamSet, i.e. the one active before this epoch's votes are applied.

Because this baseline does not change mid-epoch (governance only takes effect at the next epoch boundary via `getExpectedGovernance`/`GovsToHistory`) [2](#0-1) [3](#0-2) , two different votes cast in the same epoch — one lowering `UpperBoundBaseFee` and another raising `LowerBoundBaseFee` — are each checked independently against the *old* baseline and both pass, even though their combined effect produces `LowerBoundBaseFee > UpperBoundBaseFee`. `VerifyGov` then only checks that the aggregated governance bytes match what is deterministically derivable from the accepted votes [4](#0-3) ; it performs no cross-parameter sanity check on the final combined `ParamSet`.

### Impact Explanation
The resulting inverted bound is fed directly into `NextMagmaBlockBaseFee`, which computes clamped values using both `LowerBoundBaseFee` and `UpperBoundBaseFee` [5](#0-4) . With `Lower > Upper`, the clamping logic (`if parentBaseFee >= upperBoundBaseFee ... else if parentBaseFee <= lowerBoundBaseFee ...`) degenerates: nearly every `parentBaseFee` falls into the "clamp to upper" branch, permanently locking the network base fee to the (now too-low) upper bound and defeating the intended fee-market mechanism. This is a network-wide, economically significant miscalculation of the base fee governing every transaction's minimum gas price — directly analogous to the reported issue's "dilute/inflate at admin's discretion" impact, but here achieved purely through validly-formatted, individually-accepted governance votes.

### Likelihood Explanation
Any validator/council member able to cast governance votes (a reachable, in-scope actor per the "governance parameters" category) can trigger this by having two council members cast one `Kip71LowerBoundBaseFee` vote and one `Kip71UpperBoundBaseFee` vote within the same epoch such that, individually, each passes the stale-baseline check but together they invert the bounds. No special privilege beyond normal vote submission is required, and the check that should prevent this (`checkConsistency`) exists but is provably insufficient because it never validates against the co-pending vote or the final aggregated `ParamSet`.

### Recommendation
Validate `LowerBoundBaseFee <= UpperBoundBaseFee` against the epoch's *fully aggregated* pending vote set (i.e., the result of `getExpectedGovernance`) rather than against the previous epoch's committed baseline, or re-validate the combined `ParamSet` in `VerifyGov` before accepting a new epoch's governance block.

### Proof of Concept
1. Assume default `LowerBoundBaseFee = 25e9`, `UpperBoundBaseFee = 750e9` (baseline at epoch start).
2. Council member A casts a vote `Kip71UpperBoundBaseFee = 26e9`. `checkConsistency` checks `26e9 < params.LowerBoundBaseFee(25e9)`? No → accepted.
3. Council member B casts a vote `Kip71LowerBoundBaseFee = 27e9` (same epoch, baseline unchanged). `checkConsistency` checks `27e9 > params.UpperBoundBaseFee(750e9)`? No → accepted.
4. At epoch boundary, `getExpectedGovernance` aggregates both votes into the same `GovData`, producing `LowerBoundBaseFee = 27e9 > UpperBoundBaseFee = 26e9`, committed via `VerifyGov`'s deep-equal check with no cross-field validation.
5. From then on, `NextMagmaBlockBaseFee` computes base fees using this inverted bound configuration [6](#0-5) , breaking the fee-market invariant network-wide.

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L112-153)
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

**File:** kaiax/gov/headergov/history.go (L11-34)
```go
// GovsToHistory generates history based on sorted gov blocks.
func GovsToHistory(govs map[uint64]GovData) History {
	gh := make(map[uint64]gov.ParamSet)

	// we must ensure that gov history is not empty
	gh[0] = *gov.GetDefaultGovernanceParamSet()

	sortedNums := make([]uint64, 0, len(govs))
	for num := range govs {
		sortedNums = append(sortedNums, num)
	}
	slices.Sort(sortedNums)

	gp := *gov.GetDefaultGovernanceParamSet()
	for _, num := range sortedNums {
		govData := govs[num]
		if err := gp.SetFromMap(govData.Items()); err != nil {
			continue
		}
		gh[num] = gp
	}

	return gh
}
```

**File:** params/kip71_config.go (L80-128)
```go
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
