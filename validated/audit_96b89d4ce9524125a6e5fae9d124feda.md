### Title
Overfill-protection ceiling is computed but never enforced, letting a manipulated venue price drain solver funds in `IntentGatewayV2` fills - (File: `sdk/packages/simplex/src/strategies/fx.ts`)

### Summary
`FXFiller.calculateProfitability` (the pricing/sizing path Simplex uses to bid on and fill `IntentGatewayV2` orders) computes an `overfillCeiling = output.amount * (1 + maxOverfillBps) / 10000` and explicitly documents that this bounds "per-leg loss when the filler's internal pricing is wrong (bug, stale cache, manipulated venue)" — this is exactly the promised, published behavior (`docs/content/developers/evm/intent-gateway/simplex.mdx:335-345`: "the filler clamps its computed output to at most `(1 + maxOverfillBps) × user-requested amount`"). In the actual code, the comparison against `overfillCeiling` only emits a `logger.warn` and the clamp is a no-op: `policyMaxOutput = rawPolicyMaxOutput` is used unclamped for the rest of the fill computation (`sdk/packages/simplex/src/strategies/fx.ts:594-617`). This mirrors the H-02 bug class precisely: a safety check exists in form (the ratio/threshold comparison is computed and even logged) but does not actually constrain the value it is supposed to bound, so the documented invariant silently fails to hold.

### Finding Description
`resolveLegRates` (`fx.ts:1138-1175`) prices a leg either from a static curve or, for "curveless" USD-stable pairs, from a live venue (Uniswap V4) quote validated by an *optional* `checkPriceGuard` (`fx.ts:414-434`). The guard is disabled by default (no `priceGuard` configured per chain), and even when configured it only rejects quotes outside a static `maxDeviationBps` band around an operator-set `referencePrice` — a stale/never-updated `referencePrice` or an unconfigured chain lets an arbitrarily wrong venue price straight through.

Downstream, `calculateProfitability` uses that (possibly manipulated) rate to compute `rawPolicyMaxOutput` via `computeLegPolicyOutput`, then:

```
const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
const policyMaxOutput = rawPolicyMaxOutput
if (rawPolicyMaxOutput > overfillCeiling) {
    this.logger.warn({...}, "Overfill ceiling exceeded — clamp disabled, filling unclamped amount")
}
```
(`fx.ts:600-617`)

The variable `policyMaxOutput` is assigned the *unclamped* `rawPolicyMaxOutput` regardless of the ceiling check's outcome. Everything downstream (`walletContribution`, `credited` from funding venues, `effectiveBalance`, `finalOutputAmount`) is capped only by wallet/venue balance, never by `overfillCeiling`. The `maxConsecutiveClamps` self-halt mechanism described in the docs ("After `maxConsecutiveClamps` consecutive orders where the clamp activated, the HyperFX strategy halts itself") is also dead: since the clamp never activates, the halt condition can never be tripped, removing the documented last-resort circuit breaker as well.

This is the same root cause as H-02: a computed ratio/threshold check (`rawPolicyMaxOutput` vs. `overfillCeiling`, analogous to Curve's `a/b` and `a/c` ratio checks) exists and is even logged, but doesn't actually gate the state transition (the fill amount), so the invariant it's meant to enforce ("solver never overpays by more than 5% due to a manipulated/stale price") does not hold in practice.

### Impact Explanation
An attacker who can influence the venue price feeding a curveless leg (e.g. a thin/manipulated Uniswap V4 pool used for `getVenueUsdPrice`, or simply relying on the price-guard being unconfigured for that chain, which is the default) can cause `rawPolicyMaxOutput` to be computed far above the order's requested output. Since the overfill clamp is disabled, the filler will pay out the full unclamped amount (bounded only by its own wallet balance and configured funding venues) directly to the order's beneficiary via `IntentGatewayV2.fillOrder`, resulting in uncapped loss of solver funds through the intent-settlement fill path — precisely the class of loss the feature exists to prevent, and precisely what the shipped documentation promises is bounded at 5%. This is a logic attack in the intent-settlement fill logic that can cause fund loss for anyone running the shipped `Simplex` filler with default or venue-priced pair configuration.

### Likelihood Explanation
Likelihood is meaningful rather than purely theoretical: `checkPriceGuard` is optional and, by the code's own comments and tests, "guard is optional" (`fx.ts:1081`, `fx.price-guard.test.ts:140-144`), so any deployment that has not explicitly configured a `priceGuard` for a chain (the documented default) has no bound at all on venue-price sizing beyond this now-broken overfill clamp. The bug requires no privileged access, malicious relayer, or protocol admin — only the ability to move a venue's quoted price (a routine DeFi price-manipulation primitive on a thin pool), which is explicitly the threat model the removed clamp calls out in its own comment ("bug / stale cache / manipulated venue price").

### Recommendation
Restore the clamp: when `rawPolicyMaxOutput > overfillCeiling`, set `policyMaxOutput = overfillCeiling` (not just log a warning), and preserve the `maxConsecutiveClamps` counting/self-halt logic so it can actually trip when the clamp activates repeatedly. Additionally, make `checkPriceGuard` mandatory (or fail-closed) for any curveless/venue-priced pair rather than optional, so venue-price sizing always has an enforced upper bound independent of the overfill ceiling.

### Proof of Concept
1. Configure Simplex with a `[[pairs]]` entry with no `bidPricePolicy`/`askPricePolicy` (curveless, venue-priced) and USD-stable `token0`, and no `priceGuard` set for the destination chain (default state).
2. Manipulate the configured Uniswap V4 pool (or wait for a stale/thin-liquidity quote) so `getVenueUsdPrice` returns a price implying `token1` is worth far less than it actually is.
3. Place (or have a colluding user place) an `IntentGatewayV2` order requesting a modest `output.amount` of `token1`.
4. `resolveLegRates` returns the manipulated `venueRate`; `computeLegPolicyOutput` computes `rawPolicyMaxOutput` far above `output.amount`.
5. `calculateProfitability` (`fx.ts:600-617`) logs "Overfill ceiling exceeded — clamp disabled, filling unclamped amount" but sets `policyMaxOutput = rawPolicyMaxOutput` unchanged.
6. The filler fills the order via `fillOrder`, paying out the unclamped, manipulated amount to the beneficiary — an amount that should have been capped at `output.amount * 1.05` per the documented overfill-protection guarantee.

Note: I could not execute this against a live deployment or fully trace every caller path in this session (e.g., whether any other guard elsewhere in the SDK independently re-clamps `fillerOutputs` before the on-chain call); the finding is based on static code/comment evidence in `sdk/packages/simplex/src/strategies/fx.ts:594-617` and the documented contract in `docs/content/developers/evm/intent-gateway/simplex.mdx:335-345`. A Devin session with the ability to run the test suite (`sdk/packages/simplex/src/tests/strategies/fx.price-guard.test.ts` and any overfill-specific tests) would be needed to confirm there is no compensating clamp elsewhere.