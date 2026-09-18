No vulnerability found for this question.

The reported bug is specific to Ajna's unique grant-distribution mechanism—screening/funding periods, a "top ten proposals" slate, and a slate-update rule that only requires a strictly greater sum of funding votes with no minimum vote threshold, allowing dust-vote proposals to claim unallocated budget. Sei-chain's on-chain governance uses the standard Cosmos SDK `x/gov` module, which requires proposals to clear an explicit `quorum` and `threshold` before they can pass and execute, as seen in the genesis parameters (`quorum: 0.334`, `threshold: 0.5`, `veto_threshold: 0.334`) [1](#0-0)  and in the tally logic that rejects proposals failing quorum or majority thresholds [2](#0-1) . Treasury/community-pool spending in sei-chain is only reachable via a `CommunityPoolSpendProposal`, which must go through this same quorum/threshold-gated governance process, whether submitted directly or via the EVM gov precompile [3](#0-2) . There is no "slate" concept, no per-period budget allocation among competing proposals ranked by raw vote sum, and no code path where a dust-vote proposal can be quietly slipped in to claim unallocated treasury funds—every spend proposal must independently clear the same quorum and majority bar. This bug class does not map onto any of the listed reachable Sei-chain surfaces (EVM tx pipeline, precompiles, CW/pointer bridge, tokenfactory, oracle, OCC, JSON-RPC), so it has no valid analog here.

### Citations

**File:** app/genesis/chains/pacific-1.json (L2353-2359)
```json
        "tally_params": {
          "quorum": "0.334000000000000000",
          "threshold": "0.500000000000000000",
          "veto_threshold": "0.334000000000000000",
          "expedited_quorum": "0.667000000000000000",
          "expedited_threshold": "0.667000000000000000"
        }
```

**File:** sei-cosmos/x/gov/keeper/tally.go (L147-168)
```go
	tallyParams := keeper.GetTallyParams(ctx)
	tallyResults = types.NewTallyResultFromMap(results)
	if keeper.sk.TotalBondedTokens(ctx).IsZero() {
		return false, false, tallyResults
	}

	percentVoting := totalVotingPower.Quo(keeper.sk.TotalBondedTokens(ctx).ToDec())
	if percentVoting.LT(tallyParams.GetQuorum(proposal.IsExpedited)) {
		return false, true, tallyResults
	}

	if totalVotingPower.Sub(results[types.OptionAbstain]).Equal(sdk.ZeroDec()) {
		return false, false, tallyResults
	}

	if results[types.OptionNoWithVeto].Quo(totalVotingPower).GT(tallyParams.VetoThreshold) {
		return false, true, tallyResults
	}

	if results[types.OptionYes].Quo(totalVotingPower.Sub(results[types.OptionAbstain])).GT(tallyParams.GetThreshold(proposal.IsExpedited)) {
		return true, false, tallyResults
	}
```

**File:** precompiles/gov/handler.go (L194-231)
```go
type CommunityPoolSpendProposalHandler struct {
	evmKeeper EVMKeeper
}

func (h CommunityPoolSpendProposalHandler) HandleProposal(ctx sdk.Context, proposal Proposal) (govtypes.Content, error) {
	if proposal.CommunityPoolSpend == nil {
		return nil, errors.New("community pool spend parameters must be specified")
	}

	// Validate that the recipient is a valid Ethereum address
	if !common.IsHexAddress(proposal.CommunityPoolSpend.Recipient) {
		return nil, fmt.Errorf("invalid ethereum address format")
	}

	// Parse the amount
	amount, err := sdk.ParseCoinsNormalized(proposal.CommunityPoolSpend.Amount)
	if err != nil {
		return nil, fmt.Errorf("invalid amount format: %w", err)
	}

	if amount.IsZero() {
		return nil, errors.New("amount must be greater than zero")
	}

	// Convert Ethereum address to Sei address using the EVM keeper
	ethAddr := common.HexToAddress(proposal.CommunityPoolSpend.Recipient)
	seiAddr, found := h.evmKeeper.GetSeiAddress(ctx, ethAddr)
	if !found {
		return nil, fmt.Errorf("no sei address found for ethereum address %s", ethAddr.Hex())
	}

	return distrtypes.NewCommunityPoolSpendProposal(
		proposal.Title,
		proposal.Description,
		seiAddr,
		amount,
	), nil
}
```
