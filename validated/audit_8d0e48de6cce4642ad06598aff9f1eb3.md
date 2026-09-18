### Title
Unrecovered `sdk.Dec` overflow panic in oracle cross-rate reconstruction crashes all validating nodes - ([File: x/oracle/abci.go])

### Summary
`MidBlocker` in the oracle module reconstructs the base/quote exchange rate for non-reference denoms with `exchangeRateRD.Quo(exchangeRate)`. Unlike the structurally identical division in `ExchangeRateBallot.ToCrossRate`, this call is **not** wrapped in a `defer/recover`, so a crafted set of oracle votes that makes the divisor extremely small (but not exactly zero) causes `sdk.Dec.Quo` to overflow and panic. Because `MidBlocker`/`EndBlocker` run deterministically inside consensus block processing on every validating node, the panic is not sandboxed per-transaction and will crash the node process identically across the network, producing a chain-wide halt.

### Finding Description
`x/oracle/abci.go` computes, for each non-reference denom, the cross rate and then converts it back to base/quote terms: [1](#0-0) 

The intermediate cross rates are produced by `ExchangeRateBallot.ToCrossRate`, which explicitly acknowledges that the division can overflow and guards against it: [2](#0-1) 

However, the final "transform back" division at `exchangeRateRD.Quo(exchangeRate)` (`x/oracle/abci.go:94`) has no equivalent recovery. `exchangeRate` here is the weighted median (via `Tally`) of the cross-rated ballot, which is guarded only against being exactly `0` (`x/oracle/abci.go:86-90`), not against being a very small but non-zero value. `sdk.Dec.Quo` internally does: [3](#0-2) 

`assertInValidRange()` panics (`"Dec overflow"`, not a division-by-zero panic, but the same overflow-driven-division bug class as CVE-2018-5816: an operand whose magnitude was not properly bounded feeds directly into a division, producing an out-of-range/undefined result) when the quotient's magnitude exceeds `maxDecBitLen`. If `exchangeRateRD` (reference-denom median rate) is close to the maximum representable `Dec` and `exchangeRate` (the reconstructed cross-median) is very small, the quotient can exceed the valid range and panic.

The existing test `TestOverflowAndDivByZero` demonstrates the module authors were already aware overflow/div-by-zero conditions are reachable from oracle votes, and shows the `ToCrossRate` recover path successfully neutralizes one such case by zeroing the vote — but it does not exercise a scenario where the *reconstruction* division at line 94 is reached with a non-zero, extremely small `exchangeRate` (which is possible when votes are crafted so that the per-voter cross-rate median ends up small without any single cross-rate computation itself overflowing, since a per-vote overflow would already be recovered to `0` inside `ToCrossRate`, but many small non-overflowing per-vote cross rates can still median down to a tiny non-zero value). [4](#0-3) 

### Impact Explanation
`MidBlocker`/`EndBlocker` execute during ABCI block processing, deterministically, on every full/validating node processing that block (given the same set of aggregate votes). An unrecovered Go panic inside this code path is not treated as a transaction-level error recoverable by `runTx`; it propagates out of the module's `EndBlock`/`MidBlock` handler. Since this occurs identically on all nodes evaluating the same block, it results in a synchronized crash across the network — a full validator/node halt, satisfying the "validator halt" / "block delay beyond 2.5 seconds" / "permanent chain split" impact criteria (nodes that crash and are manually restarted with different patched logic could diverge, or the network simply stalls until an emergency upgrade).

### Likelihood Explanation
Triggering requires cooperation of oracle price feeders (validators/designated feeders) submitting aggregate exchange rate votes with carefully chosen magnitudes across at least two whitelisted denoms so that: (1) the reference denom's weighted median rate is very large, and (2) the reconstructed cross-median for another denom is a very small non-zero value. This is squarely within the "oracle vote admission" surface explicitly called in-scope, and is reachable via the standard `MsgAggregateExchangeRateVote` message-processing pipeline rather than any p2p/consensus-message exploit. It does not require exploiting network/gossip layers — only crafting vote payload magnitudes, which is a legitimate module message.

### Recommendation
Wrap the `exchangeRateRD.Quo(exchangeRate)` reconstruction at `x/oracle/abci.go:94` in the same `defer/recover` pattern used in `ExchangeRateBallot.ToCrossRate`, treating an overflow result the same way a zero-exchange-rate result is treated today (skip setting the base exchange rate for that denom instead of panicking). Additionally, consider adding an explicit upper/lower bound sanity check on `exchangeRate` before performing the division, and add a regression test that forces the reconstruction division itself (not just the per-vote `ToCrossRate` division) into overflow to confirm the fix.

### Proof of Concept
1. Whitelist two denoms, e.g. `uatom` and `ueth`, as in `TestOverflowAndDivByZero`.
2. Have enough voting power submit aggregate votes such that:
   - The `uatom` (reference-denom) votes yield a weighted median `exchangeRateRD` close to `sdk.Dec`'s maximum valid value (e.g. near `7.9e58`, similar to `overflowRate` used in the existing test but chosen so it does **not** itself trigger overflow when used as a plain median).
   - The `ueth` votes are chosen per-voter so that each individual cross rate computed inside `ToCrossRate` (`exchangeRateRT.Quo(vote.ExchangeRate)`) stays within valid `Dec` range (i.e., does not panic/get zeroed there), but the weighted median of those cross rates (computed by `Tally`) is extremely small and non-zero (e.g. `1e-18`).
3. Call `oracle.MidBlocker(ctx, k)` at the end of the vote period.
4. At `x/oracle/abci.go:94`, `exchangeRate = exchangeRateRD.Quo(exchangeRate)` computes `~7.9e58 / 1e-18 ≈ 7.9e76`, which exceeds `maxDecBitLen`, triggering the unrecovered `"Dec overflow"` panic and crashing the node's block processing.

Note: I was not able to fully verify within available tool calls whether `BaseApp`'s `EndBlock`/`MidBlock` invocation wraps a `recover()` at a higher level in this specific fork (I found no such recover in the oracle module itself, and standard Cosmos SDK `BaseApp.EndBlock` typically does not recover panics the way `DeliverTx` does). If such a top-level recover exists elsewhere in `app/abci.go` or `baseapp`, the practical impact would be reduced to a single missed block/vote-period rather than a full crash, but the underlying missing-recover defect in the division itself remains valid and should still be fixed.

### Citations

**File:** x/oracle/abci.go (L76-99)
```go
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

**File:** x/oracle/types/ballot.go (L48-75)
```go
// ToCrossRate return cross_rate(base/exchange_rate) ballot
func (pb ExchangeRateBallot) ToCrossRate(bases map[string]sdk.Dec) (cb ExchangeRateBallot) {
	for i := range pb {
		vote := pb[i]

		if exchangeRateRT, ok := bases[string(vote.Voter)]; ok && vote.ExchangeRate.IsPositive() {
			// Quo will panic on overflow, so we wrap it in a defer/recover
			func() {
				defer func() {
					if r := recover(); r != nil {
						// if overflow, set exchange rate to 0 and power to 0
						vote.ExchangeRate = sdk.ZeroDec()
						vote.Power = 0
					}
				}()
				vote.ExchangeRate = exchangeRateRT.Quo(vote.ExchangeRate)
			}()
		} else {
			// If we can't get reference Sei exchange rate, we just convert the vote as abstain vote
			vote.ExchangeRate = sdk.ZeroDec()
			vote.Power = 0
		}

		cb = append(cb, vote)
	}

	return
}
```

**File:** sei-cosmos/types/decimal.go (L296-307)
```go
// quotient
func (d Dec) Quo(d2 Dec) Dec {
	// multiply precision twice
	mul := new(big.Int).Mul(d.i, precisionReuse)
	mul.Mul(mul, precisionReuse)

	quo := new(big.Int).Quo(mul, d2.i)
	chopped := chopPrecisionAndRound(quo)
	result := Dec{chopped}
	result.assertInValidRange()
	return result
}
```

**File:** x/oracle/abci_test.go (L692-750)
```go
func TestOverflowAndDivByZero(t *testing.T) {
	input := setup(t)
	params := input.OracleKeeper.GetParams(input.Ctx)
	params.Whitelist = types.DenomList{
		{Name: utils.MicroAtomDenom},
		{Name: utils.MicroEthDenom},
	}
	input.OracleKeeper.SetParams(input.Ctx, params)

	// Set vote targets
	input.OracleKeeper.ClearVoteTargets(input.Ctx)
	input.OracleKeeper.SetVoteTarget(input.Ctx, utils.MicroAtomDenom)
	input.OracleKeeper.SetVoteTarget(input.Ctx, utils.MicroEthDenom)

	// Test overflow case
	overflowRate := sdk.MustNewDecFromStr("7896044618658097711785492504343953926634992332820282019728792003956564819967.999999999999999999")
	smallRate := sdk.MustNewDecFromStr("0.000000000000000001")
	overflowVote := sdk.DecCoins{
		sdk.NewDecCoinFromDec(utils.MicroAtomDenom, overflowRate),
		sdk.NewDecCoinFromDec(utils.MicroEthDenom, smallRate),
	}
	makeAggregateVote(t, input, overflowVote, 0)
	makeAggregateVote(t, input, overflowVote, 1)
	makeAggregateVote(t, input, overflowVote, 2)

	// This should not panic
	oracle.MidBlocker(input.Ctx, input.OracleKeeper)
	oracle.EndBlocker(input.Ctx, input.OracleKeeper)

	// Verify no exchange rates were set for overflowed one
	rate, _, _, err := input.OracleKeeper.GetBaseExchangeRate(input.Ctx, utils.MicroAtomDenom)
	require.NoError(t, err)
	require.Equal(t, overflowRate, rate)
	_, _, _, err = input.OracleKeeper.GetBaseExchangeRate(input.Ctx, utils.MicroEthDenom)
	require.Error(t, err)

	input.Ctx = input.Ctx.WithBlockHeight(1)

	// Test divide by zero case
	zeroVote := sdk.DecCoins{
		sdk.NewDecCoinFromDec(utils.MicroAtomDenom, smallRate),
		sdk.NewDecCoinFromDec(utils.MicroEthDenom, overflowRate),
	}
	makeAggregateVote(t, input, zeroVote, 0)
	makeAggregateVote(t, input, zeroVote, 1)
	makeAggregateVote(t, input, zeroVote, 2)

	// This should not panic
	oracle.MidBlocker(input.Ctx, input.OracleKeeper)
	oracle.EndBlocker(input.Ctx, input.OracleKeeper)

	// Verify no exchange rates were set for either case
	rate, height, _, err := input.OracleKeeper.GetBaseExchangeRate(input.Ctx, utils.MicroAtomDenom)
	require.NoError(t, err)
	require.Equal(t, smallRate, rate)
	require.Equal(t, int64(1), height.Int64())
	_, _, _, err = input.OracleKeeper.GetBaseExchangeRate(input.Ctx, utils.MicroEthDenom)
	require.Error(t, err)
}
```
