## Title
Extreme oracle cross-rate ratios round to zero and are persisted unchecked, enabling zero-price exploitation by downstream swappers/lenders - (File: x/oracle/abci.go)

### Summary
`MidBlocker` in `x/oracle/abci.go` computes each denom's exchange rate relative to a "reference denom" using `sdk.Dec.Quo`, which has only 18 decimals of precision. It checks for a zero result *before* the cross-rate division but never re-checks after the division, so an extremely skewed price ratio between the reference denom and a target denom can round the final published exchange rate to exactly zero. That zero rate is then persisted to the store and exposed to any public consumer (CosmWasm contracts via `wasmbinding`, EVM contracts via the oracle precompile) that trusts the on-chain price feed for pricing swaps, loans, or liquidations — mirroring the exact bug class described in the report (extreme price ratio rounds to 0, enabling free asset extraction).

### Finding Description
In `MidBlocker`, for every non-reference denom, the code computes a tallied exchange rate and checks it for zero, but the actual value that gets stored is a *further* division that happens after that check: [1](#0-0) 

```go
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
oracleMetrics.priceUpdateTotal.Add(...)
k.SetBaseExchangeRateWithEvent(ctx, denom, exchangeRate)
```

The zero-guard only protects the *pre-transform* value. The line `exchangeRate = exchangeRateRD.Quo(exchangeRate)` is the actual base/quote cross-rate that gets persisted, and `sdk.Dec.Quo` (18-decimal fixed point) rounds any quotient smaller than `1e-18` down to exactly zero: [2](#0-1) 

If `exchangeRateRD` (reference denom's own rate) is small relative to `exchangeRate` (the target denom's tallied rate) by a factor greater than roughly `1e18:1`, the resulting cross-rate silently becomes `0`, and `SetBaseExchangeRateWithEvent` stores it without any additional validation: [3](#0-2) 

This zero price is then directly readable by any unprivileged transaction:
- CosmWasm contracts querying the oracle module via `wasmbinding/queries.go`.
- EVM contracts/callers via the oracle precompile's `getExchangeRates`/`getOracleTwaps` methods, e.g. [4](#0-3) .

Any downstream logic (lending/liquidation/swap contracts) that treats "price == 0" as "this asset is worthless" or fails to explicitly reject a zero price can be tricked into valuing collateral at zero or valuing debt at zero, letting a caller extract funds for free — the same failure mode the external report describes for the Splits oracle.

There is a similar defensive pattern already present in `ExchangeRateBallot.ToCrossRate` (per-vote cross rate), where a panic from overflow is caught and the vote is zeroed out and its power dropped so it doesn't corrupt the weighted median: [5](#0-4) 

but this only guards individual votes against *overflow* panics, not the final published rate against *underflow-to-zero* rounding, which is exactly the gap in `MidBlocker`.

### Impact Explanation
A published `0` exchange rate for a denom is a systemic, protocol-level price-feed corruption: any CosmWasm or EVM contract built on Sei that consumes `x/oracle` prices (via `wasmbinding` queries or the oracle precompile) for collateral valuation, swap pricing, or liquidation thresholds can be driven to treat that asset as free or worthless. This directly satisfies the "unauthorized transfer / fund loss via precompile" bar: an attacker who can influence the vote such that the reference-denom rate becomes very small relative to a target denom's rate (or simply waits for genuinely divergent real-world prices to occur, as in the BTC/SHIB example from the source report) can drain value from any downstream contract that has not itself hardened against a zero oracle price.

### Likelihood Explanation
This does not require a malicious validator majority or governance takeover — it only requires prices to legitimately diverge by more than ~1e18:1 between the current reference denom and another whitelisted denom (or a chain of cross-rates producing that effect through `pickReferenceDenom`/multiple `VotePeriod`s), the same "rare but plausible" scenario acknowledged in the source report for real-world token pairs (e.g., BTC/SHIB ~1e11:1, already within a few orders of magnitude of the danger zone). Because Sei whitelists arbitrary denoms and the reference denom is chosen dynamically by `pickReferenceDenom`, the ratio between any two whitelisted denoms is not bounded by the protocol, making this reachable through ordinary validator voting over time without any single validator needing to act maliciously.

### Recommendation
In `x/oracle/abci.go`'s `MidBlocker`, add an explicit zero check immediately after the cross-rate transform, before calling `SetBaseExchangeRateWithEvent`:
```go
if denom != referenceDenom {
    exchangeRate = exchangeRateRD.Quo(exchangeRate)
}
if exchangeRate.IsZero() {
    continue
}
k.SetBaseExchangeRateWithEvent(ctx, denom, exchangeRate)
```
Additionally, downstream consumers of oracle prices (wasmbinding queries, oracle precompile) should treat a `0` exchange rate as "no valid price" rather than a literal price of zero.

### Proof of Concept
1. Whitelist two denoms A (reference) and B such that the tallied vote rate for B relative to A exceeds `1e18` in ratio (e.g., due to legitimate extreme price divergence, similar to BTC/SHIB scaled further, or via gradual validator votes over several `VotePeriod`s).
2. During `MidBlocker`, `Tally` computes a non-zero `exchangeRate` for B, passing the pre-transform zero check.
3. `exchangeRate = exchangeRateRD.Quo(exchangeRate)` rounds to `0` because `sdk.Dec` only carries 18 decimal digits of precision.
4. `SetBaseExchangeRateWithEvent(ctx, "B", 0)` persists a `0` price for denom B on-chain.
5. Any contract that queries this price via `wasmbinding` (CosmWasm) or the oracle precompile (`getExchangeRates`) receives `0` for denom B and can be exploited to treat B as worthless/free in any pricing logic it performs, leading to fund loss for users of that contract.

### Citations

**File:** x/oracle/abci.go (L83-99)
```go
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

**File:** x/oracle/keeper/keeper.go (L93-101)
```go
func (k Keeper) SetBaseExchangeRateWithEvent(ctx sdk.Context, denom string, exchangeRate sdk.Dec) {
	k.SetBaseExchangeRate(ctx, denom, exchangeRate)
	ctx.EventManager().EmitEvent(
		sdk.NewEvent(types.EventTypeExchangeRateUpdate,
			sdk.NewAttribute(types.AttributeKeyDenom, denom),
			sdk.NewAttribute(types.AttributeKeyExchangeRate, exchangeRate.String()),
		),
	)
}
```

**File:** precompiles/oracle/legacy/v620/oracle.go (L100-116)
```go
func (p PrecompileExecutor) getExchangeRates(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) ([]byte, uint64, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 0); err != nil {
		return nil, 0, err
	}
	exchangeRates := []DenomOracleExchangeRatePair{}
	p.oracleKeeper.IterateBaseExchangeRates(ctx, func(denom string, rate types.OracleExchangeRate) (stop bool) {
		exchangeRates = append(exchangeRates, DenomOracleExchangeRatePair{Denom: denom, OracleExchangeRateVal: OracleExchangeRate{ExchangeRate: rate.ExchangeRate.String(), LastUpdate: rate.LastUpdate.String(), LastUpdateTimestamp: rate.LastUpdateTimestamp}})
		return false
	})

	bz, err := method.Outputs.Pack(exchangeRates)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
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
