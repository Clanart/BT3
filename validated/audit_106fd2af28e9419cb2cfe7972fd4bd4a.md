### Title
FXFiller's overfill "clamp" is computed but never enforced — filler pays out unbounded amounts on manipulated venue prices - ([File: sdk/packages/simplex/src/strategies/fx.ts])

### Summary
The Simplex intent filler's `FXFiller` strategy is documented and configured to bound per-leg output loss to `(1 + maxOverfillBps) × user-requested amount` — a guard meant to protect against bad pricing (stale cache, manipulated AMM venue, bug). In the current code, the ceiling is still *computed*, but the enforcement step that used to cap the output has been removed: the raw, unclamped `policyMaxOutput` is used regardless of whether it exceeds the ceiling, and exceeding it only produces a log warning. This is the same bug class as the reported `UniProxy.properDepositRatio` issue: a safety check exists syntactically and appears to gate behavior, but the actual enforced value is never constrained to the checked bound, so the guard passes/no-ops exactly in the scenarios it was built to stop.

### Finding Description
`FXFiller.evaluateProfitability` (in `sdk/packages/simplex/src/strategies/fx.ts`) computes an overfill ceiling from the user-requested output and the configured `maxOverfillBps`: [1](#0-0) 

```
const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
const policyMaxOutput = rawPolicyMaxOutput
if (rawPolicyMaxOutput > overfillCeiling) {
    this.logger.warn(... "Overfill ceiling exceeded — clamp disabled, filling unclamped amount")
}
```

`policyMaxOutput` is assigned the raw, unclamped value (`rawPolicyMaxOutput`) unconditionally — the comparison against `overfillCeiling` only logs a warning; it never reduces the amount actually paid out. This `policyMaxOutput` then flows directly into the fill amount sent to the beneficiary via `fillerOutputs.push({ token: output.token, amount: finalOutputAmount })`.

The documentation and shipped default config describe this exact code path as an active safety bound: [2](#0-1) 
- "the filler clamps its computed output to at most `(1 + maxOverfillBps) × user-requested amount`" and the halt-after-N-consecutive-clamps mechanism.

The comment in the code itself confirms the regression: "Overfill detection is warn-only: the clamp is DISABLED... NOTE: this removes the per-leg loss bound that previously protected against a bug / stale cache / manipulated venue price."

`resolveLegRates`/`rates.rate` for venue-priced legs (e.g., Uniswap V4) is exactly the input this bound was meant to sanity-check against — a legitimate, unprivileged trader can move a thin/low-liquidity venue's spot price with an ordinary swap (no relayer/prover/admin compromise needed), causing `rawPolicyMaxOutput` computed from that skewed price to blow past the intended ceiling. Because the clamp is a no-op, the filler still fills at the inflated amount.

This is structurally identical to the reported bug: `properDepositRatio` computed `depositRatio`/`hypeRatio` and a "guard" (`FullMath.mulDiv(...) < depositDelta`) that was supposed to reject skewed deposits, but the underlying clamp to `[0.1, 10]` made the guard blind to the true magnitude of imbalance — the check existed but didn't stop the bad case. Here, the ceiling check exists and correctly identifies the bad case (`rawPolicyMaxOutput > overfillCeiling`), but the enforcement (clamping the actual paid amount) was deleted, leaving the guard purely cosmetic.

### Impact Explanation
This directly causes fund loss for the filler operator (which is part of Hyperbridge's intent-settlement infrastructure): the filler's own logic quantifies exactly how much it is being overpaying, logs it, and then pays it out anyway to the order's beneficiary. Since intent settlement is per-order and beneficiary-controlled, an attacker who can move a thin venue's spot price (a standard, permissionless DeFi action) can extract more output tokens than their input is worth, repeatedly, since the halt-after-`maxConsecutiveClamps` circuit breaker is also disabled (`recordOrderOutcome(false, ...)` is always called with `clamped=false`, so the halt subsystem never triggers). This is unauthorized/asymmetric fund transfer through legitimate-looking intent fills, matching "stealing or loss of funds" / "logic attacks" in scope.

### Likelihood Explanation
High for any deployment using the Uniswap V4-priced FXFiller pairs (the `[vault.uniswapV4]` funding source is a documented, first-class feature). No relayer, prover, or admin compromise is required — only manipulation of a venue's spot price via ordinary swaps/flash swaps, which is a routine DeFi attack primitive. The vulnerability is present in shipped code and contradicts the shipped documentation/config comments, so operators relying on the documented protection are silently unprotected.

### Recommendation
Restore actual enforcement: set `policyMaxOutput = Decimal.min(rawPolicyMaxOutput, overfillCeiling)` (or equivalent) before it is used to build `fillerOutputs`, and re-enable `recordOrderOutcome(clamped, order.id)` with the true clamped state so the consecutive-clamp halt breaker functions as documented. Add a regression test asserting that a leg priced above the ceiling results in an output capped at `overfillCeiling`, not the raw computed value.

### Proof of Concept
1. Configure an `FXFiller` pair backed by a Uniswap V4 venue with limited liquidity (`[vault.uniswapV4]`), default `maxOverfillBps = 500`.
2. Attacker performs an ordinary swap against the venue pool to push its spot price so that quoting the leg at that price yields `rawPolicyMaxOutput` > 105% of the order's requested output amount.
3. Attacker (or any user) submits an intent order sized to that pair; `resolveLegRates` returns the manipulated venue rate.
4. In `evaluateProfitability` (`fx.ts:600-617`), `rawPolicyMaxOutput > overfillCeiling` is true, a warning is logged, but `policyMaxOutput` is left equal to `rawPolicyMaxOutput`.
5. The filler proceeds to fund and fill the leg at the unclamped, inflated `finalOutputAmount`, paying the beneficiary more than the intended 5% ceiling — with `consecutiveClamps` never incrementing, so the halt safeguard never engages even across repeated exploitation.

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

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L335-345)
```text
### Overfill Protection

Bounds per-leg loss if the filler's internal pricing is wrong (bug, stale cache, manipulated venue). For every order, the filler clamps its computed output to at most `(1 + maxOverfillBps) × user-requested amount` — this ceiling applies to every strategy. After `maxConsecutiveClamps` consecutive orders where the clamp activated, the **HyperFX** strategy halts itself — a pattern that strongly suggests a systemic pricing error — and requires an operator restart. HyperFX additionally rejects any order where the total output USD value ≥ total input USD value.

Defaults are sensible (`maxOverfillBps = 500` ≈ 5% ceiling, `maxConsecutiveClamps = 3`). Override under `[simplex.overfillProtection]`:

```toml lineNumbers
[simplex.overfillProtection]
maxOverfillBps       = 500   # 5% ceiling above user-requested output
maxConsecutiveClamps = 3     # halt threshold (HyperFX)
```
```
