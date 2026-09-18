No vulnerability found for this question.

Sei's oracle module is fundamentally different from Tellor's optimistic dispute-based oracle model that the report targets. Sei uses validator-submitted `MsgAggregateExchangeRateVote` transactions that are tallied via weighted median in `MidBlocker`/`EndBlocker` at fixed `VotePeriod` intervals, with no dispute window, no permissionless price submission, and no "future peek" latency comparable to Tellor's 30-minute dispute buffer [1](#0-0) [2](#0-1) . Notably, the design was specifically changed via `MidBlock` so that oracle votes are finalized *within* the same block, before other transactions execute, precisely to eliminate the type of stale-price arbitrage window described in the report [3](#0-2) . There is no permissionless "submit/dispute" mechanism reachable by an ordinary transaction sender, contract, or RPC client — only bonded validators can vote, and the exchange rate is a network-consensus value, not third-party externally-tipped data. Since the report's core issues (dispute-window arbitrage and cheap staleness attacks against a decentralized optimistic oracle) do not map onto Sei's validator-vote oracle architecture, and third-party-oracle-data issues are explicitly out of scope, no valid analog exists.

### Citations

**File:** x/oracle/abci.go (L52-99)
```go
		})

		// Organize votes to ballot by denom
		// NOTE: **Filter out inactive or jailed validators**
		// NOTE: **Make abstain votes to have zero vote power**
		voteMap := k.OrganizeBallotByDenom(ctx, validatorClaimMap)
		// belowThresholdVoteMap has assets that failed to meet threshold
		referenceDenom, belowThresholdVoteMap := pickReferenceDenom(ctx, k, voteTargets, voteMap)

		if referenceDenom != "" {
			// make voteMap of Reference denom to calculate cross exchange rates
			ballotRD := voteMap[referenceDenom]
			voteMapRD := ballotRD.ToMap()

			exchangeRateRD := ballotRD.WeightedMedianWithAssertion()

			// Iterate through ballots and update exchange rates; drop if not enough votes have been achieved.
			keys := make([]string, len(voteMap))
			j := 0
			for denom := range voteMap {
				keys[j] = denom
				j++
			}
			sort.Strings(keys)
			for _, denom := range keys {
				ballot := voteMap[denom]
				// Convert ballot to cross exchange rates
				if denom != referenceDenom {
					ballot = ballot.ToCrossRateWithSort(voteMapRD)
				}

				// Get weighted median of cross exchange rates
				exchangeRate := Tally(ctx, ballot, params.RewardBand, validatorClaimMap)

				// if exchange rate is somehow 0, exclude it from ballot?
				if exchangeRate.IsZero() {
					// skip this denom
					continue
				}

				// Transform into the original form base/quote
				if denom != referenceDenom {
					exchangeRate = exchangeRateRD.Quo(exchangeRate)
				}

				// Set the exchange rate, emit ABCI event
				oracleMetrics.priceUpdateTotal.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("denom", denom)))
				k.SetBaseExchangeRateWithEvent(ctx, denom, exchangeRate)
```

**File:** x/oracle/spec/03_end_block.md (L7-27)
```markdown
## Tally Exchange Rate Votes

At the end of every block, the `Oracle` module checks whether it's the last block of the `VotePeriod`. If it is, it runs the [Voting Procedure](./01_concepts.md#Voting_Procedure):

1. All current active Sei exchange rates are purged from the store

2. Received votes are organized into ballots by denomination. Abstained votes, as well as votes by inactive or jailed validators are ignored

3. Denominations not meeting the following requirements will be dropped:

    - Must appear in the permitted denominations in `Whitelist`
    - Ballot for denomination must have at least `VoteThreshold` total vote power

4. For each remaining `denom` with a passing ballot:

    - Tally up votes and find the weighted median exchange rate and winners with `tally()`
    - Iterate through winners of the ballot and add their weight to their running total
    - Set the Sei exchange rate on the blockchain for that Sei<>`denom` with `k.SetSeiExchangeRate()`
   - Emit a `exchange_rate_update` event

5. Count up the validators who [missed](./01_concepts.md#Slashing) the Oracle vote and increase the appropriate miss counters
```

**File:** x/oracle/spec/MidBlock.md (L5-15)
```markdown
Currently, the oracle module processes oracle pricing votes from validators via transactions, and executes them as part of the normal DeliverTx. Then, the oracle module calculates the oracle asset pricing in End Block once it has processed all oracle vote transactions for the block. This has the behavior where new oracle prices are stored as part of EndBlock, and so aren’t used by transactions or contracts until the following block.

When working with a voting period of 1 we can make further optimizations to improve the freshness of oracle asset pricing. We would like to have oracle votes finalized BEFORE the execution of non-oracle vote txs, such that the newest oracle asset pricing is finalized WITHIN the block so that transactions and contracts are utilizing the freshest oracle prices possible.

## Design

Currently, transaction execution is performed as part of ProcessTxs, which is performed after BeginBlock and before EndBlock. The approach to improve oracle data freshness is to first filter the transactions into oracle votes and non-oracle votes. Then, we initially begin ProcessTxs specifically for the oracle votes. Then, we introduce a new stage called MidBlock, which modules can implement in order to perform some logic after ProcessOracleTxs and before other transactions.

Practically, this is a fairly straightforward solution because we can use the same DeliverTx logic to execute the two groups of transactions, and would only need to partition the transactions appropriately. Additionally, because we can use the existing DeliverTx logic, we get parallelization for the oracle transactions out of the box, which is a slight performance improvement since all oracle votes can be executed in parallel (although practically not significantly different performance due to low oracle vote volume).

With this approach, an oracle vote for block N would be processed separately from other TXs in block N, and the MidBlock function that separates the two can be used to calculate oracle consensus based on validators votes. This way, any transaction in block N would utilize the oracle price updated in the block N midblock IFF the oracle vote had enough total voting power.
```
