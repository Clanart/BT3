## Analog Found: KIP-71 base-fee bound votes can be independently set to a crossed (Lower > Upper) configuration

### Title
Independent votes for `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` in the same epoch can cross, producing an invalid base-fee range - (File: `kaiax/gov/headergov/impl/header.go`)

### Summary
`checkConsistency()` validates each governance vote for the KIP-71 base-fee bounds only against the currently active `ParamSet`, not against other votes accepted within the same epoch. This mirrors the reported `setTargetThreshold`/`minThreshold` bug class: a setter enforces an ordering invariant against a stale reference value instead of the value that will actually be in effect after all pending updates are applied, allowing the invariant (`LowerBoundBaseFee <= UpperBoundBaseFee`) to be violated.

### Finding Description
When a vote for `gov.Kip71LowerBoundBaseFee` or `gov.Kip71UpperBoundBaseFee` is verified, the check is performed against `h.GetParamSet(blockNum)` — the parameter set derived from already-finalized governance, not from any other vote pending in the same epoch: [1](#0-0) 

Both checks compare the new vote value against the *current* opposite bound, which has not yet been updated by a co-pending vote for the same epoch. Since only one `header.Vote` is proposed per block [2](#0-1) , but multiple blocks/votes accumulate within a single epoch and are later merged in `getExpectedGovernance()` [3](#0-2) , two separately-valid votes proposing `LowerBoundBaseFee = X` and `UpperBoundBaseFee = Y` (with `X > Y`) can each pass `checkConsistency` individually (each compared to the old, not-yet-updated opposite bound), yet together produce `LowerBoundBaseFee > UpperBoundBaseFee` once both take effect at the next epoch.

### Impact Explanation
`NextMagmaBlockBaseFee()` assumes `LowerBoundBaseFee <= UpperBoundBaseFee` when clamping the parent base fee: [4](#0-3) 

With a crossed configuration, the clamping logic can force the computed base fee below the intended `LowerBoundBaseFee` (or above the intended `UpperBoundBaseFee`), silently violating the governance-configured floor/ceiling for transaction pricing. Since every node applies the same corrupted `KIP71Config` deterministically, this does not cause a fork, but it does defeat the intended fee-floor guarantee, allowing transactions to be priced/accepted below the value governance intended as the minimum, which is a fee-mechanism integrity failure.

### Likelihood Explanation
This requires governance voting rights (governing node in single mode, or council votes in ballot mode) to submit two conflicting votes within the same epoch. This is within the explicitly in-scope "governance parameters" analog category, but it does require privileged governance capability rather than a fully permissionless actor, which somewhat limits the practical likelihood of exploitation to a rare/edge-case operational error or a compromised/malicious governing node.

### Recommendation
When validating a vote for `Kip71LowerBoundBaseFee` or `Kip71UpperBoundBaseFee`, also account for any other vote(s) already accepted within the same epoch (e.g., check against the pending merged `getExpectedGovernance` state, not just the last finalized `ParamSet`), or enforce the ordering invariant again at the epoch-application step in `VerifyGov`/`getExpectedGovernance` before committing the merged governance data.

### Proof of Concept
1. Assume current `ParamSet`: `LowerBoundBaseFee = 25_000_000_000`, `UpperBoundBaseFee = 750_000_000_000`.
2. Within the same epoch, the proposer casts vote A: `Kip71LowerBoundBaseFee = 700_000_000_000`. `checkConsistency` compares `700_000_000_000 > params.UpperBoundBaseFee (750_000_000_000)` → false → vote accepted.
3. Later in the same epoch, the proposer (or another council member, in ballot mode) casts vote B: `Kip71UpperBoundBaseFee = 100_000_000_000`. `checkConsistency` compares `100_000_000_000 < params.LowerBoundBaseFee (25_000_000_000)` → false (still using the old, pre-vote-A value) → vote accepted.
4. At the epoch boundary, `getExpectedGovernance` merges both votes, producing `LowerBoundBaseFee = 700_000_000_000 > UpperBoundBaseFee = 100_000_000_000`, an invalid crossed configuration accepted into `header.Governance` without any final consistency check.

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L30-39)
```go
func (h *headerGovModule) PrepareHeader(header *types.Header) error {
	// if this node has a vote waiting to be casted, put Vote field.
	if vote, ok := h.peekMyVote(); ok {
		voteBytes, err := vote.ToVoteBytes()
		if err != nil {
			return err
		}
		header.Vote = voteBytes
		logger.Debug("Prepare header with vote", "num", header.Number.Uint64(), "vote", hexutil.Encode(header.Vote))
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

**File:** kaiax/gov/headergov/impl/header.go (L227-242)
```go
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

**File:** params/kip71_config.go (L80-86)
```go
	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}
```
