### Title
Overfill protection clamp disabled in HyperFX filler lets a manipulated venue price drain solver funds on intent fills - ([File: sdk/packages/simplex/src/strategies/fx.ts])

### Summary
The external report's core broken invariant is: a formula/guard that used to cap a computed value (the veCRV/pool-share boost) at a safe maximum was removed, so the optimizer could compute an unbounded value and misallocate funds based on it. The local analog is in Hyperbridge's reference intent-filling engine (`HyperFX`, `sdk/packages/simplex/src/strategies/fx.ts`), which fills cross-chain/same-chain intents placed on `IntentGatewayV2`. The filler used to clamp its computed output at `(1 + maxOverfillBps) × user-requested amount` specifically to bound loss from a bad price ("bug, stale cache, manipulated venue"). That clamp has been intentionally disabled and downgraded to a warning-only log, so the filler now pays out the full unclamped `policyMaxOutput` even when a venue price is manipulated.

### Finding Description
In `computeLegOutput` (the per-leg fill sizing loop, `sdk/packages/simplex/src/strategies/fx.ts:594-617`), the code computes `overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n` and compares it against `rawPolicyMaxOutput`, but instead of clamping:

```
sdk/packages/simplex/src/strategies/fx.ts:600-617
const overfillCeiling = (output.amount * (10000n + this.maxOverfillBps)) / 10000n
const policyMaxOutput = rawPolicyMaxOutput
if (rawPolicyMaxOutput > overfillCeiling) {
    this.logger.warn(..., "Overfill ceiling exceeded — clamp disabled, filling unclamped amount")
}
```

`policyMaxOutput` is assigned directly from `rawPolicyMaxOutput` with no `Decimal.min`/clamp applied, contradicting both the code's own comment ("Bounds per-leg loss if the filler's internal pricing is wrong") and the documented behavior in `docs/content/developers/evm/intent-gateway/simplex.mdx:335-345` ("the filler clamps its computed output to at most `(1 + maxOverfillBps) × user-requested amount`").

For venue-priced pairs (USD-stable `token0`, exotic `token1` priced off a live venue such as Uniswap V4), `rawPolicyMaxOutput` is derived from a live pool quote (`resolveLegRates`/`referenceRate`) rather than a fixed curve. An attacker who transiently manipulates that venue's spot price (e.g. via a large swap in the same block/transaction context) can inflate the quoted rate so that `computeLegPolicyOutput` returns a `policyMaxOutput` far above the user's requested output. Since the "per-leg loss bound" is disabled, nothing stops the filler from executing `fillOrder`/`_fillSameChain` on `IntentGatewayV2` (`evm/src/apps/intentsv2/IntrinsicIntents.sol`) with that inflated `solverAmount`, transferring the filler's own funds to the beneficiary at the manipulated rate — and per `_fillSameChain`'s surplus logic, any solver-supplied excess above `totalRequired` is even split as "surplus" to the beneficiary/protocol, further rewarding the attacker for triggering the overpay.

The comment in `referenceRate` (`fx.ts:1076-1082`) references a `checkPriceGuard` that defends the **order-sizing/confirmation-depth** path against pool manipulation, but that guard is separate from, and does not gate, the per-leg overfill ceiling that was supposed to bound execution-time loss. The execution-time guard is the one that has been disabled.

### Impact Explanation
This is a direct loss-of-funds path for the intent-filling solver operating Hyperbridge's reference filler: an attacker who can move a venue's spot price momentarily can cause the filler to pay out an arbitrarily inflated amount of its own escrowed/wallet/vault-funded tokens on `IntentGatewayV2`, well beyond the previously-designed 5% ceiling, with the excess even codified as "surplus" paid to the beneficiary. This matches the bounty's "stealing or loss of funds" and "logic attacks" categories: an unprivileged user (the order placer/beneficiary) can profit at the filler's expense purely by manipulating an on-chain price feed the filler's pricing formula relies on, exploiting a *removed* safety cap exactly as in the external report (min/cap removed → formula produces unbounded value → optimizer/filler misallocates real funds).

### Likelihood Explanation
Likelihood is elevated by the fact that: (1) the disabling is explicit and intentional in the current code (not a typo), meaning it is live in whatever deployment uses this default HyperFX configuration; (2) venue-priced (Uniswap V4) legs are exactly the case flagged as vulnerable in the code's own comments; (3) the attacker only needs to place an intent and briefly move a pool's price — no relayer, prover, admin, or leaked key is required, satisfying the "unprivileged attacker, public entrypoint" requirement.

### Recommendation
Reinstate the clamp: set `policyMaxOutput = Decimal.min(rawPolicyMaxOutput, overfillCeiling)` (or the bigint equivalent) instead of only logging a warning, or gate execution on venue-priced legs behind the same price-sanity guard (`checkPriceGuard`) used for order sizing, so an inflated quote cannot translate into an inflated on-chain payout.

### Proof of Concept
1. Configure a `HyperFX` filler with a venue-priced pair (`token0` USD-stable, `token1` priced via Uniswap V4, no `bidPricePolicy`/`askPricePolicy`).
2. Attacker places a cross-chain/same-chain intent on `IntentGatewayV2` requesting a modest `output.amount` of `token1`.
3. In the same block window the filler evaluates the order, attacker manipulates the Uniswap V4 pool (e.g., large swap) so the venue quote used by `referenceRate`/`resolveLegRates` reports an inflated `token1` price.
4. `computeLegPolicyOutput` returns `rawPolicyMaxOutput` far above `output.amount * (1 + maxOverfillBps)`.
5. Because the clamp at `fx.ts:594-617` is disabled, `policyMaxOutput` retains the unclamped value; the filler proceeds to fund and submit `fillOrder` on `IntentGatewayV2` with the inflated `solverAmount`.
6. `IntrinsicIntents._fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:84-95`) treats the excess as surplus and pays a share directly to `beneficiary` (the attacker), realizing an economic gain funded entirely by the filler's assets, in a magnitude the disabled ceiling was specifically designed to prevent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** sdk/packages/simplex/src/strategies/fx.ts (L1063-1083)
```typescript
	private async referenceRate(
		leg: ResolvedLeg,
		venueUsdPrice: (chain: string, token1Address: string) => Promise<Decimal | null>,
	): Promise<Decimal | null> {
		const policy = leg.pair.bidPricePolicy ?? leg.pair.askPricePolicy
		if (policy) {
			const rate = policy.getPrice(new Decimal(0))
			return rate.gt(0) ? rate : null
		}
		// Venue-priced pair: token0 is USD-stable (constructor invariant), so the
		// venue's USD-per-token1 quote inverts straight into token1-per-token0.
		const venueUsd = await venueUsdPrice(leg.token1Chain, leg.token1Address)
		if (!venueUsd) return null
		const venueRate = new Decimal(1).div(venueUsd)
		// Same guard as trade pricing: this rate sizes the order's USD notional
		// for confirmation depth, and a manipulated pool understating the value
		// would shrink the reorg protection — the exact attack the guard exists
		// to stop. Refusing to size skips the order, consistent with pricing.
		if (!this.checkPriceGuard(undefined, leg.token1Chain, venueRate)) return null
		return venueRate
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L80-99)
```text
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;
```
