### Title
Disabled overfill clamp lets a manipulated Uniswap V4 venue price drive the Simplex filler to overpay unclamped intent outputs - (File: `sdk/packages/simplex/src/strategies/fx.ts`)

### Summary
The external report's core invariant is: an instantaneous AMM reserve/price is used directly to compute a value that moves funds (voting power → reward), with no TWAP or bound, so an attacker who moves the pool within the same block can inflate that value and extract value. In Hyperbridge's intent-filling path, `FXFiller.calculateProfitability` in `sdk/packages/simplex/src/strategies/fx.ts` prices "venue" legs (curve-less pairs) directly from a live Uniswap V4 pool quote via `resolveLegRates`/`getVenueUsdPrice`, and the one guard that used to bound the resulting fill amount — the overfill ceiling — has been explicitly turned into a warn-only log rather than a hard cap.

### Finding Description
For pairs configured without static bid/ask curves, `resolveLegRates` prices the leg from the live Uniswap V4 pool ("venue-priced" legs — see `docs/content/developers/evm/intent-gateway/simplex.mdx` "Pool-Based Pricing" section and `sdk/packages/simplex/src/funding/uniswapV4/UniswapV4FundingPlanner.ts:200-234` `computeDirectPoolPriceUsd`). This spot price is then used in `computeLegPolicyOutput` to derive `rawPolicyMaxOutput`, the amount of `output.token` the filler will send to fill the order.

Historically, `overfillCeiling = output.amount * (10000 + maxOverfillBps) / 10000` capped how far above the user's requested amount the filler could go, bounding losses from a bad quote. The current code (`fx.ts:594-617`) computes the ceiling, detects a breach, but explicitly does **not** clamp:

```
// Overfill detection is warn-only: the clamp is DISABLED, so the filler
// fills the full computed amount even when it exceeds
// (1 + maxOverfillBps) × user-requested — including venue-priced legs
// (e.g. Uniswap V4). NOTE: this removes the per-leg loss bound that
// previously protected against a bug / stale cache / manipulated venue
// price. Output is no longer capped; we only emit a warning.
``` [1](#0-0) 

There is an optional `priceGuard` (`referencePrice`/`maxDeviationBps`) that rejects a fill when the venue quote deviates too far from a configured reference (`checkPriceGuard`, `fx.ts:414-434`), but per the docs this guard is **optional** ("The two fields must be set together; omit both to leave the chain unguarded") [2](#0-1)  and is a static-deviation check, not a TWAP — it only protects deployments that explicitly configure it, and even then only bounds against a *fixed* reference, not against the same manipulate-then-quote-then-revert pattern the M-10 report describes.

The corrupted value is `rates.rate` (token1-per-token0 derived from the live V4 pool tick/`slot0`), which flows unclamped into `rawPolicyMaxOutput` → the actual `fillerOutputs[i].amount` sent on-chain via `fillOrder`/ERC-7821 batch call. An attacker who swaps against the referenced Uniswap V4 pool immediately before submitting (or alongside submitting, since intent-order matching and pool state are asynchronous and not atomically bound) an intent order can push the quoted `token1 per token0` price far above fair value; the filler, lacking a hard clamp (and, if `priceGuard` is unconfigured, lacking any check at all), computes and sends an inflated output amount, extracting the difference from the filler's own inventory/vault. This exactly mirrors the YAxisVotePower pattern: an unbounded/instantaneous AMM-derived quantity feeding a fund-moving calculation, with the mitigating check present in code but non-binding.

### Impact Explanation
This causes direct loss of solver/filler funds: the filler overpays real `output.token` (potentially withdrawn from Uniswap V4 LP positions per `docs/content/developers/evm/intent-gateway/simplex.mdx` "Uniswap V4 LP funding") relative to what the order actually earns, and the on-chain fill still executes because there is no on-chain enforcement of the ceiling — the entire safety property lives in an off-chain warning log. This is a direct "logic attack" / fund-loss vector against the bridge's intent-settlement flow, matching the required impact class (loss of funds via manipulable amount, no privileged actor needed).

### Likelihood Explanation
Likelihood is elevated by two facts: (1) the clamp removal is intentional and unconditional — it applies to every venue-priced leg regardless of configuration, so any deployment relying on curve-less, Uniswap-V4-priced pairs is exposed by default; (2) the `priceGuard` mitigation is opt-in and, per the docs, chains are commonly left "unguarded." An attacker only needs capital to move the referenced pool (which for a "thin" pool explicitly called out in the docs as a risk — "leaves the filler exposed to a manipulated, stale, or thin pool") and to submit/trigger matching of an intent order while the price is skewed — no relayer, prover, or admin compromise required.

### Recommendation
Restore the overfill ceiling as a hard cap (reject or truncate the fill) rather than a warning, at minimum for venue-priced legs, and require `priceGuard` (`referencePrice`/`maxDeviationBps`) to be mandatory rather than optional whenever a pair has no static curves. Consider deriving the venue price from a time-weighted quote (average tick over a window) instead of the instantaneous `slot0`/tick, consistent with the TWAP mitigation recommended in the original report.

### Proof of Concept
1. Configure (or find in production) an FX pair with no `bidPriceCurve`/`askPriceCurve` and a `[vault.uniswapV4]` position, with `priceGuard` left unset (the documented default posture).
2. Attacker swaps in the referenced Uniswap V4 pool to skew `token1Price`/`token0Price` far from fair value.
3. Attacker (or an unrelated user, front-run by the attacker) submits/has-matched an intent order on that pair while the skewed price is live.
4. `calculateProfitability` → `resolveLegRates` reads the skewed pool price; `computeLegPolicyOutput` computes an inflated `rawPolicyMaxOutput`; the code at `fx.ts:594-617` detects `rawPolicyMaxOutput > overfillCeiling`, logs a warning, and — because the clamp is disabled — still assigns `policyMaxOutput = rawPolicyMaxOutput` to `fillerOutputs`.
5. The filler executes `fillOrder` on-chain with the unclamped, inflated output amount, realizing a loss equal to the manipulated spread; the attacker (as counterparty/beneficiary of the order) receives the excess value.

Note: I could not trace the exact downstream code that turns `fillerOutputs`/`policyMaxOutput` into the on-chain `fillOrder` call within this iteration (e.g., `executeOrder`), so the final on-chain call site is inferred from the surrounding comments and variable naming rather than directly cited; a full session with broader file access would be needed to confirm the exact transaction-construction code path.

### Citations

**File:** sdk/packages/simplex/src/strategies/fx.ts (L591-617)
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
```

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L236-248)
```text
#### Uniswap price guards

Pool-based pricing trusts the live pool, which leaves the filler exposed to a manipulated, stale, or thin pool returning a bad quote. To bound that risk, give a position a **`referencePrice`** and **`maxDeviationBps`**. Whenever the pool quote on that chain drifts more than `maxDeviationBps` above or below the reference, the filler refuses to fill — the order is rejected before any bid is submitted.

`referencePrice` is expressed in **exotic tokens per USD**, the same units as the bid/ask curves. The two fields must be set together; omit both to leave the chain unguarded.

```toml lineNumbers
[vault.uniswapV4]
positions = [
    # referencePrice is the expected cNGN per USD; reject if the quote is more than 2% off
    { chain = "EVM-8453", tokenId = "2087350", referencePrice = "1575", maxDeviationBps = 200 },
]
```
```
