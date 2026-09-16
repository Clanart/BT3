## Finding: Same-epoch KIP-71 base-fee bound votes are validated against stale values, allowing `LowerBoundBaseFee > UpperBoundBaseFee` after ratification

### Title
Governance votes for `kip71.lowerboundbasefee` and `kip71.upperboundbasefee` are validated independently against stale (pre-epoch) values instead of each other's pending value, allowing the ratified invariant `Lower ≤ Upper` to be broken - (File: `kaiax/gov/headergov/impl/header.go`)

### Summary
This mirrors the M-12 pattern in the external report: a parameter update is bounds-checked only against a single, currently-effective, independently-modifiable value rather than the value that will actually be in effect once all pending changes are combined. In Gondi, `_liquidationAuctionDuration` was checked in isolation against `MAX_AUCTION_DURATION` without accounting for `getMaxExtension`, letting the sum silently violate the intended 7-day cap. In Kaia's `headergov` module, `kip71.lowerboundbasefee` and `kip71.upperboundbasefee` votes are each checked against `h.GetParamSet(blockNum)` — the *previously ratified* parameter set — rather than against each other's newly-voted (but not-yet-ratified) value from the same epoch.

### Finding Description
`VerifyVote` calls `checkConsistency` for every vote in a block: [1](#0-0) 

`checkConsistency` cross-checks the two KIP-71 bound parameters, but only against the *currently effective* `ParamSet`, obtained via `h.GetParamSet(blockNum)`: [2](#0-1) 

`GetParamSet(blockNum)` returns the parameter set ratified from prior epochs; it does **not** reflect other votes cast earlier in the *same* epoch that have not yet been ratified at the next epoch boundary, as described in the module's own documentation: [3](#0-2) 

At epoch ratification, all votes from the epoch are simply merged into a `PartialParamSet` via `.Add()`, which only performs a format check (not a cross-parameter consistency check): [4](#0-3) [5](#0-4) 

Consequently, within the same epoch, the governing node (or, in `none` mode, any council member) can cast two valid-looking votes that pass `checkConsistency` individually but combine into an inconsistent, ratified state:
1. Vote A: set `LowerBoundBaseFee = X` where `X ≤ current UpperBoundBaseFee` — passes.
2. Vote B (same epoch, later block): set `UpperBoundBaseFee = Y` where `Y ≥ current LowerBoundBaseFee` (the *old*, still-unchanged value) — passes, even if `Y < X`.

At the next epoch boundary both votes are ratified together into `header.Governance`, producing `LowerBoundBaseFee (X) > UpperBoundBaseFee (Y)`.

### Impact Explanation
`KIP71Config.NextMagmaBlockBaseFee`, which computes every block's base fee, assumes `LowerBoundBaseFee ≤ UpperBoundBaseFee` implicitly in its clamping logic (`parentBaseFee` is clamped to whichever bound it exceeds, and the ceil/floor evening logic is only proven correct for `Lower ≤ Upper` in the existing tests): [6](#0-5) 

With `Lower > Upper`, the clamping and delta-adjustment logic no longer guarantees the base fee stays within any coherent bound — the fee could get pinned to an unintended stale value, oscillate incorrectly, or otherwise diverge from the intended KIP-71 economics used to price every transaction on the network. Since the base fee directly determines fee burn accounting and transaction admission, this can corrupt fee/burn accounting network-wide and is unrecoverable except via a subsequent hard-fix governance vote (itself subject to the same unguarded check).

### Likelihood Explanation
This requires the ability to cast two governance votes within a single epoch. In `single` governance mode, only the `governingnode` can vote (a designed, expected actor within the "governance parameters" reachable surface), and votes are ordinary `governance_vote` RPC calls followed by being included as block proposer over consecutive blocks in the same epoch — no privileged code execution or node compromise required. In `none` mode, this is even easier since any council member's last vote in the epoch is ratified per-parameter.

### Recommendation
When validating a `Kip71LowerBoundBaseFee` or `Kip71UpperBoundBaseFee` vote, compare against the **pending effective value that would result from combining it with any other unratified vote already cast in the same epoch** (i.e., the vote returned by `getExpectedGovernance` for the current, in-progress epoch merged with the new vote), not just the stale `GetParamSet(blockNum)` snapshot. Alternatively, perform a final consistency check across the fully-merged `PartialParamSet` at ratification time (in `getExpectedGovernance` / `VerifyGov`) before it is committed to `header.Governance`, rejecting any combination where `LowerBoundBaseFee > UpperBoundBaseFee`.

### Proof of Concept
1. Assume epoch `k` starts with `LowerBoundBaseFee = 25e9`, `UpperBoundBaseFee = 750e9` (mainnet defaults).
2. Governing node casts vote at block `k*epoch+1`: `kip71.lowerboundbasefee = 700e9`. `checkConsistency` compares `700e9 ≤ params.UpperBoundBaseFee (750e9)` → passes.
3. Governing node casts vote at block `k*epoch+2`: `kip71.upperboundbasefee = 100e9`. `checkConsistency` compares `100e9 ≥ params.LowerBoundBaseFee` — but `params` here is still `GetParamSet(k*epoch+2)`, which reflects the pre-epoch value `25e9`, not the pending `700e9` from step 2 → passes.
4. At epoch `k+1` boundary, both votes are ratified together: effective `LowerBoundBaseFee = 700e9`, `UpperBoundBaseFee = 100e9`, an inconsistent state that was never directly validated as a pair.
5. From block `(k+1)*epoch` onward, `NextMagmaBlockBaseFee` operates with `Lower > Upper`, breaking the intended base-fee bound invariant relied on by fee/burn accounting.

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L101-109)
```go
	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}

	return h.checkConsistency(blockNum, vote)
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

**File:** kaiax/gov/headergov/README.md (L52-60)
```markdown
### Reading a parameter set

The parameter set at block `N` (in `k`-th epoch) is determined as follows:

- Collect all the ratified parameters from 0-th to `k-1`-th epoch. In case of duplication, recent ratification is prioritized.
  - `k-1` is calculated by [PrevEpochStart](./impl/getter.go#L41).
- For each parameter, take the last ratified value. If a parameter has never been ratified, use the default value as a fallback.

This is the description of `GetParamSet(N)`, which is implemented [here](./impl/getter.go#L9).
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

**File:** params/kip71_config.go (L58-90)
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
```
