### Title
Filler overfill safety clamp is disabled (warn-only), allowing unbounded solver-fund drain via price/cache manipulation - (File: sdk/packages/simplex/src/strategies/fx.ts)

### Summary
`FXFiller.calculateProfitability` in `sdk/packages/simplex/src/strategies/fx.ts` used to hard-cap the amount a filler would output per leg at `(1 + maxOverfillBps) × user-requested`, protecting the filler's vault/wallet funds against a pricing bug, a stale cache, or a manipulated venue price. That enforcement has been reduced to a log-only warning, exactly mirroring the DynamoFinance pattern where a critical state-changing/guarding call was neutered (there, commented out; here, converted to telemetry) while the surrounding code still behaves as if the guard exists.

### Finding Description
In `calculateProfitability` (`sdk/packages/simplex/src/strategies/fx.ts:591-617`), after computing `rawPolicyMaxOutput` for a leg, the code computes an `overfillCeiling`:

```
const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
const policyMaxOutput = rawPolicyMaxOutput
if (rawPolicyMaxOutput > overfillCeiling) {
    this.logger.warn(... "Overfill ceiling exceeded — clamp disabled, filling unclamped amount")
}
``` [1](#0-0) 

Critically, `policyMaxOutput` is assigned directly from `rawPolicyMaxOutput` with no `Math.min`/clamp against `overfillCeiling`. The comment explicitly documents that this removed "the per-leg loss bound that previously protected against a bug / stale cache / manipulated venue price," yet the surrounding logic (`overfillCeiling` computed, `maxOverfillBps` configured, warn message referencing "clamp disabled") makes clear a bounding control was intended and is now inert — the same defect class as `Governance.vy`'s `replaceGovernanceContract` call being commented out: a documented, purpose-built safety mechanism exists in name/comment/telemetry only, and the code path that should enforce it never executes.

`policyMaxOutput` subsequently drives real fund movement: it is used to compute `walletContribution`, drawn from the filler's wallet balance, and any shortfall (`needed`) is sourced by calling `venue.planWithdrawalForToken(...)` against the funding venues (the vault), producing `fundingCalls` that are later executed on-chain: [2](#0-1) 

Because the clamp is disabled, any leg priced via `rates.priceSource === "venue"` (Uniswap V4-derived pricing per the module docstring) — or any leg suffering from a stale `venueUsdPrice` cache or a pricing-policy bug — can yield an arbitrarily large `rawPolicyMaxOutput` relative to what the user actually requested (`output.amount`), and that full amount is pulled from the filler's wallet and vault funding venues without limit, other than the wallet/venue balance itself.

### Impact Explanation
This directly threatens "stealing or loss of funds" and "logic attacks" in the Hyperbridge intent-settlement path: an intent order that is mispriced (via manipulated or stale venue price data, or a latent pricing bug) is no longer bounded by the `maxOverfillBps` safety margin. The filler's on-chain wallet and connected funding vault(s) can be drained per-fill up to the unclamped `policyMaxOutput`, well beyond the user's requested output, with the removed guard providing zero on-chain protection — only a log line. Since `fundingCalls` are built from `planWithdrawalForToken` against real vault liquidity, this is a genuine fund-loss vector, not a cosmetic issue.

### Likelihood Explanation
`resolveLegRates`/pricing for venue-priced legs (Uniswap V4) is inherently subject to price movement and caching (`venuePriceMemo()`), and the code path is reached on every `calculateProfitability` call for orders matching a configured pair with no bid/ask policy (reference-priced via venue). Any order that induces a spike in `rawPolicyMaxOutput` — through legitimate volatility, a stale cache read, or active price manipulation of the venue — will silently bypass the intended ceiling and proceed to fund the fill from filler/vault balances. No privileged actor, relayer collusion, or malicious peer is required; a public intent order crafted or timed to coincide with a mispriced venue quote is sufficient to trigger the unclamped payout.

### Recommendation
Restore actual enforcement of the overfill ceiling instead of only logging it:
```
const policyMaxOutput = rawPolicyMaxOutput > overfillCeiling ? overfillCeiling : rawPolicyMaxOutput
```
Keep the existing warning for observability, but ensure `policyMaxOutput` — and therefore `walletContribution`/`needed`/all downstream funding calls — is capped at `overfillCeiling` so a mispriced or manipulated leg can never authorize more than `(1 + maxOverfillBps) × output.amount` to be withdrawn from filler or vault balances.

### Proof of Concept
1. Configure/target a pair with no `bidPricePolicy`/`askPricePolicy` (venue-priced via Uniswap V4), so `rates.priceSource === "venue"`.
2. Submit (or wait for/trigger) a state where the venue price used by `venuePriceMemo()` is stale or manipulated (e.g., via a large temporary swap on the referenced Uniswap V4 pool, or exploiting the memoization window) such that `resolveLegRates` returns a rate materially favorable versus the true market rate.
3. Submit an intent order with a modest `output.amount` for that leg; `computeLegPolicyOutput` will compute `rawPolicyMaxOutput` far above `overfillCeiling = output.amount * (1 + maxOverfillBps)`.
4. Observe in `calculateProfitability` that the code only logs `"Overfill ceiling exceeded — clamp disabled, filling unclamped amount"` and sets `policyMaxOutput = rawPolicyMaxOutput` unclamped.
5. The filler proceeds to pull `walletContribution` from its wallet and issue `venue.planWithdrawalForToken` calls sized to the unclamped `policyMaxOutput`, executing an on-chain fill that pays out far more than the safety margin intended — draining filler/vault funds to the counterparty of the manipulated order. [3](#0-2)

### Citations

**File:** sdk/packages/simplex/src/strategies/fx.ts (L591-648)
```typescript
				const { token0Used, policyMaxOutput: rawPolicyMaxOutput } = legResult
				remainingByPair.set(leg.pair, remaining.minus(token0Used))

				// Overfill detection is warn-only: the clamp is DISABLED, so the filler
				// fills the full computed amount even when it exceeds
				// (1 + maxOverfillBps) × user-requested — including venue-priced legs
				// (e.g. Uniswap V4). NOTE: this removes the per-leg loss bound that
				// previously protected against a bug / stale cache / manipulated venue
				// price. Output is no longer capped; we only emit a warning.
				const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
				const policyMaxOutput = rawPolicyMaxOutput
				if (rawPolicyMaxOutput > overfillCeiling) {
					this.logger.warn(
						{
							orderId: order.id,
							leg: i,
							pair: `${leg.pair.token0}/${leg.pair.token1}`,
							token: output.token,
							userRequested: output.amount.toString(),
							unclamped: rawPolicyMaxOutput.toString(),
							ceiling: overfillCeiling.toString(),
							maxOverfillBps: this.maxOverfillBps.toString(),
							priceSource: rates.priceSource,
						},
						"Overfill ceiling exceeded — clamp disabled, filling unclamped amount",
					)
				}

				// Spend the free wallet balance first, down to the configured minBalance
				// reserve — kept liquid for the gas/paymaster pull during
				// validatePaymasterUserOp — then source any remaining shortfall from the
				// funding venues (the vault).
				const tokenAddress = bytes32ToBytes20(output.token).toLowerCase()
				const balance = await this.getAndCacheBalance(tokenAddress, walletAddress, destClient, balanceCache)

				let reserve = 0n
				for (const venue of this.fundingVenues) {
					reserve += venue.walletReserveForToken(destChain, tokenAddress)
				}
				const usableWallet = balance > reserve ? balance - reserve : 0n

				const walletContribution = policyMaxOutput < usableWallet ? policyMaxOutput : usableWallet

				let credited = 0n
				let needed = policyMaxOutput - walletContribution
				for (const venue of this.fundingVenues) {
					if (needed <= 0n) break
					const planned = await venue.planWithdrawalForToken(destChain, walletAddress, tokenAddress, needed, deadlineTimestamp)
					if (planned.calls.length > 0) {
						fundingCalls.push(...planned.calls)
						credited += planned.credited
						needed -= planned.credited
					}
				}

				const effectiveBalance = walletContribution + credited

				const finalOutputAmount = effectiveBalance > policyMaxOutput ? policyMaxOutput : effectiveBalance
```
