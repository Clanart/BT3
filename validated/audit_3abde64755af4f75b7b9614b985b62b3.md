### Title
Header governance vote authorization gate is only enforced after the Permissionless fork, allowing any council proposer (not just the governing node) to ratify governance parameter changes in "single" mode - ([File: kaiax/gov/headergov/impl/header.go])

### Summary
`headerGovModule.VerifyVote` only rejects a non-governing-node vote when `governance.governancemode == "single"` **and** `IsPermissionlessForkEnabled` is true for the block. Before that fork is activated, any validator in the council that becomes block proposer can cast a `header.Vote` for a governance parameter, and that vote is accepted, recorded, and later ratified — even though the chain is configured in `single` mode where only the `governance.governingnode` should be authorized to change parameters.

### Finding Description
`VerifyVote` in [1](#0-0)  gates the single-mode voter-authorization check behind `h.ChainConfig.IsPermissionlessForkEnabled(...)`:
```
// In single mode, only the governing node can write header.Vote after Permissionless.
params := h.GetParamSet(blockNum)
if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
    params.GovernanceMode == "single" &&
    vote.Voter() != params.GoverningNode {
    return ErrVotePermissionDenied
}
```
Prior to that fork, the only checks performed are that the voter is a council member and that the voter is the block's proposer/author (lines 82-99). There is no restriction that the voter must equal `params.GoverningNode`. This is confirmed by the module's own test, `TestVerifyVote_SingleMode`, whose sub-test `"pre-permissionless allows non-governing node"` explicitly asserts `NoError` for a vote cast by a non-governing council member in single mode [2](#0-1) .

Once such a vote passes `VerifyVote`, it is stored via `HandleVote`/`AddVote` without any governing-node filter [3](#0-2) . At the next epoch boundary, `getExpectedGovernance` aggregates **all** votes recorded in the previous epoch (again, with no governing-node filter) to build the ratified `header.Governance` payload [4](#0-3) , and `VerifyGov`/execution then apply this as the new effective parameter set.

The mainnet/Kairos README states unambiguously that `single` mode restricts voting rights to the governing node ("only one member of the GC, stipulated in the parameter `governance.governingnode`, can vote") [5](#0-4) . The `IsPermissionlessForkEnabled` gate contradicts this documented invariant for any chain/period where the Permissionless fork has not yet activated (e.g. `PermissionlessCompatibleBlock` unset or in the future), which is analogous to the GitLab bug class: an authenticated-but-unauthorized principal (here, a non-governing validator who becomes proposer through normal round-robin/weighted-random rotation) can modify settings that should be restricted to a specific privileged principal (the governing node), i.e. incorrect/insufficient authorization enforcement gated by an unrelated condition (fork activation) rather than the actual governance mode semantics.

### Impact Explanation
If any council validator becomes block proposer while `governancemode=single` and the Permissionless fork has not activated, that validator can unilaterally vote to change consensus/economic parameters that are supposed to require the single governing node's authorization — including `governance.unitprice`, `kip71.lowerboundbasefee`/`upperboundbasefee`/`basefeedenominator`/`gastarget`, `reward.mintingamount`, `reward.ratio`, `reward.kip82ratio`, `istanbul.committeesize`, and `governance.govparamcontract`. These parameters directly control fee levels (state transition/gas accounting), block base-fee/KIP-71 pricing, and reward/inflation distribution. An unauthorized change ratified at an epoch boundary is written into `header.Governance` and becomes the binding parameter set for all honest nodes for the next epoch, effectively bypassing the intended single-authority governance model and enabling economic-parameter manipulation (e.g. reward redirection via `reward.ratio`/`mintingamount`, or fee manipulation via `unitprice`/KIP-71 bounds) by a non-privileged council member.

### Likelihood Explanation
Exploitability depends entirely on whether the Permissionless hard fork is active on the target network at the relevant block. On networks/periods where `PermissionlessCompatibleBlock` is unset or not yet reached (this is under operator/network configuration, not attacker control, but is a realistic historical/interim network state), the bypass requires no privilege beyond being an ordinary council validator who is naturally selected as proposer through the existing proposer-selection algorithm — no signature forgery or additional compromise is needed. This makes it a Medium-severity issue: it is not exploitable by a fully unprivileged external actor, but it grants a validator authority they should not have under "single" mode before the fork activates.

### Recommendation
Remove the `IsPermissionlessForkEnabled` condition from the single-mode voter check in `VerifyVote` (and audit `checkConsistency`'s `GovernanceGoverningNode`/`AddValidator`/`RemoveValidator` cases which have the same fork-gated pattern), so that `governancemode == "single"` always restricts `header.Vote` acceptance to `params.GoverningNode`, matching the documented invariant, regardless of fork activation status.

### Proof of Concept
1. Configure a chain with `Governance.GovernanceMode = "single"`, `Governance.GoverningNode = A`, and `PermissionlessCompatibleBlock = nil` (fork inactive) — this matches `TestVerifyVote_SingleMode`'s "pre-permissionless" sub-test setup [2](#0-1) .
2. Have council validator `B` (≠ `A`) become block proposer through normal rotation and call `governance_vote` to set `governance.unitprice` (or `reward.mintingamount`) to an attacker-favorable value; the vote is embedded into `header.Vote` via `PrepareHeader`.
3. `VerifyVote` accepts the vote since the fork-gated check is skipped, and it passes council/proposer checks.
4. At the next epoch boundary, `getExpectedGovernance` includes `B`'s vote (no governing-node filter), so it is written into `header.Governance` and ratified, becoming the effective parameter set for all nodes — despite `B` never being the designated `GoverningNode`.

### Citations

**File:** kaiax/gov/headergov/impl/header.go (L92-107)
```go
	// check if Voter is the block proposer.
	author, err := h.Chain.Sealer().Author(header)
	if err != nil {
		return err
	}
	if author != vote.Voter() {
		return ErrInvalidVoter
	}

	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
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

**File:** kaiax/gov/headergov/impl/execution.go (L44-55)
```go
func (h *headerGovModule) HandleVote(blockNum uint64, vote headergov.VoteData) error {
	// if governance vote (i.e., not validator vote), add to vote
	if _, ok := gov.Params[vote.Name()]; ok {
		h.AddVote(blockNum, vote)
		InsertVoteDataBlockNum(h.ChainKv, blockNum)
	}

	// if the vote was mine, remove it.
	h.removeMyVote(vote)

	return nil
}
```

**File:** kaiax/gov/headergov/README.md (L44-47)
```markdown
The ratification condition is determined by the `governance.governancemode` parameter. Mainnet and Kairos both operate in `single` mode. There are two governance modes:

- `none` mode: all members of the GC can vote. For each governance parameter, the last vote in the epoch will be ratified.
- `single` mode: only one member of the GC, stipulated in the parameter `governance.governingnode`, can vote. All valid votes from the governing node in the epoch are ratified in block order. For each governance parameter, the last vote in the epoch will be ratified.
```
