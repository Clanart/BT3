### Title
Protocol fee floor-division in `IntentGatewayV2.placeOrder` lets users evade protocol fees by splitting orders - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.placeOrder()` computes the protocol fee with truncating integer division, identical in shape to the DeliHook `baseFeeSpecified` bug: `protocolFee = (originalAmount * protocolFeeBps) / 10_000`. Because Solidity division floors toward zero, any `originalAmount` small enough that `originalAmount * protocolFeeBps < 10_000` yields `protocolFee = 0`. An attacker can split a single large order into many small orders to make each fee computation round down to (or near) zero, extracting the exact same "many-small-transactions-beat-fee-rounding" primitive as the DeliHook M-6 report — cumulative fee paid is strictly less than the fee that would be charged on the equivalent single order, with the difference going to the attacker/order-flow instead of the protocol.

### Finding Description
In `placeOrder`, the fee is computed and deducted per order, per input token, with plain floor division and no minimum-order-size guard: [1](#0-0) 

The same pattern is duplicated in the Tron variant of the contract: [2](#0-1) 

There is no lower bound on `order.inputs[i].amount` (only a zero-amount check), and `placeOrder` is a fully public, unprivileged entry point that can be called any number of times by the same address with no cooldown, batching limit, or per-account rate limiting other than gas cost. This mirrors exactly the DeliHook root cause: the fee formula rounds down instead of up, and the attacker's cost of driving many transactions (gas) can be made arbitrarily small relative to the aggregate fee saved when:
1. `protocolFeeBps` (governance-configurable per destination or globally, up to just under 10,000 = 100%) is set to a non-trivial value, and/or
2. the input token has a high per-unit value and/or low fee-token decimal granularity (e.g., wrapped BTC-style assets), so that `amount * protocolFeeBps / 10_000` truncates by a meaningful absolute amount on every split.

The SDK's own fee helper documents that the on-chain formula "floors" the fee (`deductProtocolFee` mirrors "the gateway's floored fee deduction"), confirming the on-chain behavior is intentional floor division rather than a bug limited to documentation: [3](#0-2) 

Notably, the SDK team already anticipated the *opposite* direction of this problem (grossing up a net amount so a fee deduction doesn't leave the net short) and implemented `divCeil`/`grossUpForProtocolFee` for that case, but no equivalent ceiling logic protects the protocol's own fee revenue on the deduction side in the Solidity contract itself: [4](#0-3) 

Unlike the DeliHook case, this analog does not steal counterparty funds directly — the escrowed `reducedAmount` a solver must match is internally consistent (`commitment` is computed over the same rounded value), so solvers aren't shortchanged relative to what the order promises. The loss is protocol fee revenue: repeatedly splitting orders lets a user pay less cumulative `protocolFee` than an equivalent single order would incur, directly reducing the `DustCollected` amount retained by the gateway.

### Impact Explanation
This is a logic/economic attack on protocol fee accounting, not a proof or custody bypass: it does not let an attacker steal escrowed solver/user funds, forge state, or double-claim. Its only effect is fee-revenue leakage proportional to the number of orders split and the configured `protocolFeeBps`. At the current default (5 bps) and typical stablecoin decimals, the truncation is economically negligible (each split saves at most a few wei-equivalent of fee), so under current deployed parameters the attack is not clearly profitable net of gas — this differs materially from the DeliHook scenario where the fee bps (300, i.e. 3%) and a high-value/low-decimal asset (wBTC) combined to make the truncation economically significant per swap. Because `protocolFeeBps` is governance-adjustable per destination, the exposure scales with configuration rather than being inherent to the code, and higher-value/high-fee configurations would reproduce the original bug's economics more closely.

### Likelihood Explanation
Low under current default parameters (5 bps, standard ERC-20 decimals) because the per-order truncation is sub-cent and gas cost on most EVM chains dominates any saved fee. Likelihood rises only if governance sets a materially higher `protocolFeeBps` for a specific destination/token pair and/or the input token has very few effective "fee units" per dollar (mirroring wBTC's role in the original report). No malicious peer, relayer, or admin is required — this is purely a self-serve, unprivileged interaction with `placeOrder`.

### Recommendation
Round the protocol fee up (ceiling division) rather than down when it is non-zero, mirroring the `divCeil` helper already present in the SDK, e.g. `protocolFee = originalAmount == 0 ? 0 : ((originalAmount * protocolFeeBps + 9_999) / 10_000)`, or explicitly enforce `protocolFee > 0` whenever `protocolFeeBps > 0 && originalAmount > 0`. Consider also enforcing a minimum order `amount` per fee-bearing input so trivially small orders cannot be used to zero out the fee entirely.

### Proof of Concept
1. Governance configures `protocolFeeBps` for a destination to a non-trivial value (e.g., 300 bps, as in the original DeliHook example) via the fee-setting path feeding `_destinationProtocolFees`/`_params.protocolFeeBps`.
2. An attacker repeatedly calls `placeOrder` with `order.inputs[i].amount` chosen just below the truncation boundary (`amount * protocolFeeBps < 10_000`), each time paying `protocolFee = 0` per the computation at: [5](#0-4) 
3. Aggregating N such orders moves the same total value as one large order but collects `0` cumulative `DustCollected` fee instead of `originalAmount_total * protocolFeeBps / 10_000`, reproducing the DeliHook "swap in a loop to avoid paying fees" pattern against the gateway's protocol-fee revenue.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L309-320)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                if (originalAmount == 0) revert InvalidInput();
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-368)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/shared.ts (L45-50)
```typescript
/** Mirrors the gateway's floored fee deduction. */
export function deductProtocolFee(amount: bigint, protocolFeeBps: bigint): bigint {
	if (protocolFeeBps <= 0n) return amount
	const fee = (amount * protocolFeeBps) / BPS_DENOMINATOR
	return amount - fee
}
```

**File:** sdk/packages/sdk/src/protocols/intents/quote/shared.ts (L52-57)
```typescript
/** Conservatively grosses a net amount up so protocol-fee deduction cannot leave it short. */
export function grossUpForProtocolFee(netAmount: bigint, protocolFeeBps: bigint): bigint {
	if (protocolFeeBps <= 0n) return netAmount
	if (protocolFeeBps >= BPS_DENOMINATOR) throw new Error("protocolFeeBps must be less than 10,000")
	return divCeil(netAmount * BPS_DENOMINATOR, BPS_DENOMINATOR - protocolFeeBps)
}
```
