### Title
Pre-Permissionless governance vote authorization bypass allows any council validator to ratify governance parameters restricted to the governing node in "single" mode - ([File: kaiax/gov/headergov/impl/header.go])

### Summary
In `single` governance mode (the mode used by both Mainnet and Kairos), governance parameter changes are supposed to be restricted to the one designated `governance.governingnode` [1](#0-0) . However, the on-chain header verification logic that enforces this restriction (`VerifyVote`) only checks the governing-node requirement when the Permissionless hardfork is active, allowing any council validator that becomes a block proposer to write and ratify arbitrary governance votes before that fork activates.

### Finding Description
`VerifyVote` is the consensus-level check that all honest nodes use to accept or reject a proposer's `header.Vote` field [2](#0-1) . The single-mode/governing-node restriction is applied only conditionally: [3](#0-2) 

```go
// In single mode, only the governing node can write header.Vote after Permissionless.
params := h.GetParamSet(blockNum)
if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
    params.GovernanceMode == "single" &&
    vote.Voter() != params.GoverningNode {
    return ErrVotePermissionDenied
}
```

This is confirmed by an explicit unit test asserting that pre-Permissionless-fork, a non-governing-node voter's vote passes verification with no error, even in single mode: [4](#0-3) 

By contrast, the RPC-facing `governance_vote` API applies the restriction unconditionally, regardless of fork state: [5](#0-4) 

This means the official API on a well-behaved node refuses to let a non-governing validator submit a vote, but this is only a client-side courtesy check. The actual authorization boundary enforced by all nodes on the network is `VerifyVote`, which — pre-Permissionless — omits the governing-node check entirely. A validator operator who is a legitimate member of the council (i.e., not modifying consensus itself, but simply constructing their own block header content, which is exactly what the block-proposer role is allowed to do) can bypass the client's `governance_vote` gate and directly inject an arbitrary `(paramName, value)` vote into `header.Vote` when it is their turn to propose. Because `VerifyVote` will accept it as valid on all honest nodes (no `ErrVotePermissionDenied`), and `checkConsistency` (called from `VerifyVote`) does not perform an equivalent single-mode/governing-node check for general parameters (only for `GovernanceGoverningNode`, `Kip71LowerBoundBaseFee/UpperBoundBaseFee`, and `AddValidator/RemoveValidator`) [6](#0-5) , the vote becomes eligible to be the "last vote in the epoch" and gets ratified into `header.Governance` by the module's execution logic exactly like a legitimate governing-node vote would.

This is directly analogous to the referenced GitLab CVE-2021-22186: a lower-privileged actor (a validator, analogous to a "group maintainer") is able to modify settings that are supposed to be restricted to a higher-privileged actor (the governing node, analogous to a "group owner"), because the authorization check is missing/incomplete on the enforcement path that matters (block header vote verification), even though it is present on a separate, non-authoritative path (the RPC API).

### Impact Explanation
Any council validator, not just the governing node, can effectively set arbitrary governance parameters — including `governance.unitprice` (transaction gas price floor), `reward.mintingamount`, `reward.ratio`, `reward.kip82ratio`, `istanbul.committeesize`, or even `governance.governingnode` itself (subject to the narrower `checkConsistency` rule) — while `IsPermissionlessForkEnabled` is false for the relevant block. This can be used to redirect block rewards, alter fee/staking economics, or otherwise manipulate chain-wide economic parameters that are supposed to require single-governing-node consensus, constituting unauthorized value movement/reward redirection and violating the documented trust model of "single" governance mode.

### Likelihood Explanation
Exploitation requires only that the attacker be a member of the current validator council and wait for their turn to be block proposer — no special privilege beyond ordinary validator participation is needed, and no p2p/consensus-message forgery or malicious peer behavior is required (the attacker legitimately builds their own block header). The precondition is that the Permissionless hardfork has not yet activated for the relevant block height; whether this currently applies to Mainnet/Kairos block heights depends on live fork-activation configuration, which could not be verified from the indexed code alone.

### Recommendation
Remove the `IsPermissionlessForkEnabled` gate around the single-mode governing-node check in `VerifyVote` (or otherwise ensure the check is unconditionally enforced for all blocks under `single` governance mode), so that the header-level authorization matches the intended trust model regardless of hardfork status. Add an explicit unit test asserting that pre-Permissionless-fork votes from non-governing nodes are rejected in single mode, mirroring the existing post-Permissionless test.

### Proof of Concept
1. Configure/observe a chain in `governance.governancemode = "single"` with a designated `governance.governingnode`, on a block height where `IsPermissionlessForkEnabled(blockNum)` is false.
2. As any validator in the council other than the governing node, when it is your turn to be block proposer, directly populate `header.Vote` with an arbitrary `VoteData{Voter: self, Name: "reward.mintingamount", Value: <attacker-chosen amount>}` instead of going through the `governance_vote` RPC (which would reject it).
3. Broadcast the block. All honest nodes call `VerifyVote`, which — because `IsPermissionlessForkEnabled` is false — skips the `vote.Voter() != params.GoverningNode` check [3](#0-2)  and accepts the vote as valid.
4. At the next epoch boundary, this vote (being the "last" for that parameter name in the epoch) is ratified into `header.Governance` and becomes the effective network-wide parameter, confirmed by `VerifyGov`/`getExpectedGovernance` matching logic [7](#0-6) , without the governing node ever having approved it.

### Citations

**File:** kaiax/gov/headergov/README.md (L44-47)
```markdown
The ratification condition is determined by the `governance.governancemode` parameter. Mainnet and Kairos both operate in `single` mode. There are two governance modes:

- `none` mode: all members of the GC can vote. For each governance parameter, the last vote in the epoch will be ratified.
- `single` mode: only one member of the GC, stipulated in the parameter `governance.governingnode`, can vote. All valid votes from the governing node in the epoch are ratified in block order. For each governance parameter, the last vote in the epoch will be ratified.
```

**File:** kaiax/gov/headergov/impl/header.go (L57-65)
```go
// VerifyVote checks the following:
// (1) voter must be in valset,
// (2) integrity of the voter (the voter must be the block proposer),
// (3) the vote value must be consistent compared to the latest ParamSet.
func (h *headerGovModule) VerifyVote(header *types.Header) error {
	if len(header.Vote) == 0 {
		return nil
	}

```

**File:** kaiax/gov/headergov/impl/header.go (L101-107)
```go
	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}
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

**File:** kaiax/gov/headergov/impl/header.go (L156-223)
```go
// checkConsistency checks if vote values are consistent with chain states such as other parameters and validator set.
func (h *headerGovModule) checkConsistency(blockNum uint64, vote headergov.VoteData) error {
	switch vote.Name() {
	case gov.GovernanceGoverningNode:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}

		council, err := h.ValSet.GetCouncil(blockNum)
		if err != nil {
			return err
		}

		if !slices.Contains(council, params.GoverningNode) {
			return ErrGovNodeNotInValSetList
		}
		if !h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) {
			return nil
		}

		// After Permissionless only the governing node may vote, so a successor outside the council could never vote again.
		newNode, ok := vote.Value().(common.Address)
		if !ok || common.EmptyAddress(newNode) {
			return ErrInvalidKeyValue
		}
		if !slices.Contains(council, newNode) {
			return ErrGovNodeNotInValSetList
		}
		return nil
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
	case gov.AddValidator, gov.RemoveValidator:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}
		if slices.Contains(vote.Value().([]common.Address), params.GoverningNode) {
			return ErrGovNodeInValSetVoteValue
		}
		return nil
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
	default:
		return ErrInvalidKeyValue
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

**File:** kaiax/gov/headergov/impl/api.go (L53-63)
```go
func (api *headerGovAPI) Vote(name string, value any) (string, error) {
	var (
		voter     = api.h.nodeAddress
		nextBlock = api.h.Chain.CurrentBlock().NumberU64() + 1
		gp        = api.h.GetParamSet(nextBlock)
		gMode     = gp.GovernanceMode
	)

	if gMode == "single" && voter != gp.GoverningNode {
		return "", ErrVotePermissionDenied
	}
```
