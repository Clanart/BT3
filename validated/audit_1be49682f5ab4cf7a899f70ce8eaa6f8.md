Based on the analysis performed, I found a concrete authorization gap analogous to the Jenkins Gerrit Trigger Plugin issue (unauthorized modification of privileged configuration by an actor who should only have limited/no rights to do so).

### Title
Governance vote ratification does not filter by `governingnode` in single mode — non-governing council members' votes get ratified - (File: `kaiax/gov/headergov/impl/header.go`)

### Summary
In `single` governance mode, only the `governance.governingnode` is supposed to have the right to change governance parameters via header voting [1](#0-0) . However, the vote-collection/ratification logic in `getExpectedGovernance`/`getVotesInEpoch` blindly aggregates **all** votes recorded in `h.groupedVotes` for the epoch without re-checking `GovernanceMode`/`GoverningNode` at ratification time [2](#0-1) .

### Finding Description
The only authorization gate for who may cast a vote that gets written into `header.Vote` lives in `VerifyVote`, and even there the single-mode restriction (only the governing node can vote) is explicitly only enforced **after** the Permissionless hard fork: [3](#0-2) 

Before `PermissionlessCompatibleBlock` is enabled, `VerifyVote` allows **any council member acting as block proposer** to insert a `governance.*` vote into the header even while the chain is configured in `single` mode — confirmed by the test `TestVerifyVote_SingleMode`: [4](#0-3) 

Once such a vote is accepted into `h.groupedVotes` for the epoch, `getExpectedGovernance` (called by both `PrepareHeader` when a node produces the epoch block, and `VerifyGov` when validating it) aggregates every vote in the epoch with no governing-node/GovernanceMode filter at all: [5](#0-4) [6](#0-5) 

This means that in the pre-Permissionless window, any council member (not just the governing node) who becomes proposer for even one block in an epoch can inject a governance parameter change (e.g. `governance.unitprice`, `kip71.lowerboundbasefee`/`upperboundbasefee`, `reward.mintingamount`, `reward.ratio`, `istanbul.committeesize`) that will be ratified at the next epoch boundary and become the chain's effective parameter set — bypassing the "only governing node decides" authorization model documented for `single` mode.

### Impact Explanation
Ratified governance parameters directly control unit price/base fee bounds, reward minting amount and ratio, and committee size — i.e., fee economics, block reward issuance, and consensus committee composition. An unauthorized council member (who under the documented model should have no governance authority in `single` mode) forcing a parameter change is a supply/fee-accounting and consensus-parameter integrity issue: it can redirect rewards, alter fee levels applied to all transaction senders, or change consensus committee sizing — all from an ordinary validator's normal block-production duty, no p2p/consensus-message exploit or private key leak required.

### Likelihood Explanation
Requires only being a member of the current validator council (not the governing node) and becoming a block proposer for at least one block during an epoch — a routine, expected occurrence under round-robin/sticky/weighted-random proposer selection, and is entirely achievable pre-Permissionless-fork by design of the code path, not an edge-case race.

### Recommendation
Enforce the `single`-mode governing-node restriction unconditionally in `VerifyVote` (remove or narrow the `IsPermissionlessForkEnabled` gate at [7](#0-6) ), and/or add an equivalent authorization filter directly inside `getExpectedGovernance`/`getVotesInEpoch` so ratification itself re-validates that each aggregated vote's voter was authorized under the `GovernanceMode`/`GoverningNode` in effect at the time of the vote, rather than trusting `VerifyVote`'s point-in-time (and version-gated) check alone.

### Proof of Concept
1. Deploy/join a chain configured with `governance.governancemode = "single"` and `governance.governingnode = A`, on a chain height before `PermissionlessCompatibleBlock`.
2. As council member `B` (≠ A), when selected as proposer for a block within an epoch, call `governance_vote` to queue a vote for e.g. `governance.unitprice` — the API-level check only rejects this post-hoc based on `GetParamSet` but per `TestVerifyVote_SingleMode` pre-permissionless case, header-level `VerifyVote` accepts it since the governing-node gate is skipped pre-fork.
3. `PrepareHeader` writes `B`'s vote into `header.Vote`; other nodes' `VerifyHeader`/`VerifyVote` accept it (pre-fork skip).
4. At the next epoch boundary, `getExpectedGovernance` aggregates `B`'s vote with no re-check of governing node, and it becomes the ratified `header.Governance`, changing chain-wide parameters set by an unauthorized (non-governing) council member.

### Citations

**File:** kaiax/gov/headergov/README.md (L44-47)
```markdown
The ratification condition is determined by the `governance.governancemode` parameter. Mainnet and Kairos both operate in `single` mode. There are two governance modes:

- `none` mode: all members of the GC can vote. For each governance parameter, the last vote in the epoch will be ratified.
- `single` mode: only one member of the GC, stipulated in the parameter `governance.governingnode`, can vote. All valid votes from the governing node in the epoch are ratified in block order. For each governance parameter, the last vote in the epoch will be ratified.
```

**File:** kaiax/gov/headergov/impl/header.go (L41-52)
```go
	// if epoch block & vote exists in the last epoch, put Governance field.
	if header.Number.Uint64()%h.epoch == 0 {
		gov := h.getExpectedGovernance(header.Number.Uint64())
		if len(gov.Items()) > 0 {
			govBytes, err := gov.ToGovBytes()
			if err != nil {
				return err
			}
			header.Governance = govBytes
			logger.Debug("Prepare header with governance", "num", header.Number.Uint64(), "governance", hexutil.Encode(header.Governance))
		}
	}
```

**File:** kaiax/gov/headergov/impl/header.go (L100-109)
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

**File:** kaiax/gov/headergov/impl/header_test.go (L124-135)
```go
func TestVerifyVote_SingleMode(t *testing.T) {
	config := getTestChainConfig()
	config.Governance.GoverningNode = common.Address{2} // validVoter is the proposer, but not governing node
	t.Run("pre-permissionless allows non-governing node", func(t *testing.T) {
		config.PermissionlessCompatibleBlock = nil
		h := newHeaderGovModule(t, config)
		vote := headergov.NewVoteData(validVoter, string(gov.GovernanceUnitPrice), uint64(100))
		vb, err := vote.ToVoteBytes()
		require.NoError(t, err)
		err = h.VerifyVote(&types.Header{Number: big.NewInt(1), Vote: vb, Extra: extra})
		assert.NoError(t, err)
	})
```
