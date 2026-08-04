## Analysis

The External Report's core broken invariant is: *"a stale/unreliable price source is used to move funds instead of reverting/bounding the outcome, because a safety clamp was removed."*

The closest verifiable local analog is in the FX filler strategy used by Hyperbridge's intent solvers (`sdk/packages/simplex`), which is production code that directly determines how much of a filler's real funds get sent on-chain via `fillOrder`.

In `sdk/packages/simplex/src/strategies/fx.ts`, `calculateProfitability()` computes `rawPolicyMaxOutput` from `resolveLegRates`, which for venue-priced legs pulls a live venue quote (e.g., a Uniswap V4 pool mid-price) via `venueUsdPrice`/`referenceRate`. The code explicitly documents that an overfill clamp that used to bound this value was intentionally disabled: [1](#0-0) 

The `policyMaxOutput` is set equal to the unclamped `rawPolicyMaxOutput` with no enforcement — only a warning log — even when it exceeds `(1 + maxOverfillBps) × output.amount`. This value then directly drives the wallet/vault funding logic and the amount transferred to the counterparty in the filled order: [2](#0-1) 

This is structurally identical to the reported bug class: a price source that can be stale, wrong, or momentarily manipulated (the comment explicitly names "a bug / stale cache / manipulated venue price") is consumed without a revert/bound, and the previously-existing guard that would have stopped it was removed rather than kept as a hard limit.

### Title
FX filler overfill clamp disabled lets a manipulated/stale venue price drain filler funds - (File: sdk/packages/simplex/src/strategies/fx.ts)

### Summary
The `FXFiller.calculateProfitability` overfill guard was converted from an enforced cap into a warn-only log, so `policyMaxOutput` for venue-priced legs is used unclamped even when it wildly exceeds the user's requested output plus configured slippage tolerance.

### Finding Description
For venue-priced legs, `resolveLegRates` derives a rate from `venueUsdPrice`, a live on-chain quote (e.g. Uniswap V4). `computeLegPolicyOutput` turns that rate into `rawPolicyMaxOutput`. The code computes `overfillCeiling = output.amount * (1 + maxOverfillBps)` explicitly to bound the output, but then assigns `policyMaxOutput = rawPolicyMaxOutput` unconditionally — the ceiling is only used to decide whether to log a warning, never to clamp the actual value used downstream: [3](#0-2) 
That unclamped `policyMaxOutput` then determines how much of the filler's wallet balance and vault/funding-venue credit is committed to `finalOutputAmount`, which becomes the amount actually paid out on `fillOrder`: [4](#0-3) 
Because a venue quote can be transiently distorted (thin liquidity, flash-loan-assisted single-block manipulation, or a stale cached quote from `venuePriceMemo()`), an order that triggers this leg can cause the filler to compute and pay out an output amount far above the amount it should ever accept — with no on-chain or off-chain hard stop, only a log line.

### Impact Explanation
This directly causes loss of the filler's real on-chain funds: the solver executes `fillOrder` with an inflated `solverAmount` derived from a manipulated/stale venue price, transferring assets to the order's beneficiary far in excess of the intended and economically justified amount. This matches the required impact class "stealing or loss of funds" / "transaction manipulation" via a corrupted value (`policyMaxOutput`) feeding directly into an on-chain fund transfer, with no unprivileged-caller requirement beyond placing an ordinary order that happens to route through a manipulable venue-priced pair.

### Likelihood Explanation
Requires: (1) a pair configured with no curve policies (venue-priced) per `resolveLegRates`'s `curveless` branch, and (2) an attacker able to move the referenced venue's price briefly (e.g. via a flash swap) at the moment the filler quotes/fills, or a stale cached quote from `venuePriceMemo()`. Both conditions are plausible in a live deployment using DEX-quote fallback pricing, and the code comment itself acknowledges the disabled clamp "previously protected against a bug / stale cache / manipulated venue price," indicating the maintainers are aware this exact scenario is unguarded.

### Recommendation
Re-enable the overfill clamp as a hard bound rather than a warning: when `rawPolicyMaxOutput > overfillCeiling`, either clamp `policyMaxOutput` to `overfillCeiling` or reject the leg (skip filling) instead of logging and proceeding — mirroring the Chainlink sequencer-downtime pattern of reverting/refusing to act on an untrusted value rather than silently using it.

### Proof of Concept
1. Configure a curveless (venue-priced) pair for token0/token1 relying on `venueUsdPrice` (e.g., Uniswap V4 mid quote), consistent with the `curveless` branch in `resolveLegRates`.
2. Attacker briefly manipulates the venue pool price (flash swap in the same block, or exploits `venuePriceMemo()` staleness) so `venueUsd` under-reports token1's USD value.
3. Attacker places an order whose output leg uses this pair.
4. `calculateProfitability` computes `rawPolicyMaxOutput` from the manipulated rate; `overfillCeiling` check fires but only logs a warning per [3](#0-2) .
5. `policyMaxOutput` (unclamped) flows into `walletContribution`/`credited`/`finalOutputAmount` at [4](#0-3) , and the filler fills the order with the inflated amount, transferring excess funds to the attacker-controlled recipient once the manipulated price reverts to normal.

### Citations

**File:** sdk/packages/simplex/src/strategies/fx.ts (L594-617)
```typescript
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
```

**File:** sdk/packages/simplex/src/strategies/fx.ts (L626-648)
```typescript
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
