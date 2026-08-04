Based on the evidence gathered, I found a direct, locally-provable analog to the M02 bug class in the `IntentGatewayV2` protocol-fee deduction path.

### Title
Protocol fee on intent orders rounds to zero for small input amounts, letting orders systematically avoid fee payment - (File: evm/src/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2` deducts a protocol fee from each order input using Solidity integer division, exactly the pattern flagged in the external report. When the computed fee truncates to zero, the order proceeds with the full, undiminished input amount and no fee is collected, with no validation or revert to prevent this.

### Finding Description
When an order is placed, the gateway resolves the applicable `protocolFeeBps` and computes each input's protocol fee as: [1](#0-0) 

```solidity
if (protocolFeeBps > 0) {
    reducedInputs = new TokenInfo[](inputsLen);
    for (uint256 i; i < inputsLen;) {
        uint256 originalAmount = order.inputs[i].amount;
        if (originalAmount == 0) revert InvalidInput();
        uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
        uint256 reducedAmount = originalAmount - protocolFee;
        ...
        if (protocolFee > 0) emit DustCollected(token, protocolFee);
```

The only guard present is `originalAmount == 0 → revert InvalidInput()`; there is no check that the *computed fee* is non-zero. Because `protocolFee = (originalAmount * protocolFeeBps) / 10_000` uses integer division, any `originalAmount < 10_000 / protocolFeeBps` truncates the fee to `0`, and `reducedAmount` equals `originalAmount` unchanged — the order is escrowed and filled at full value with zero protocol fee taken. This mirrors the exact primitive of the M02 report: fee = percentage × amount / denominator, rounding down to zero for amounts below the denominator threshold, with the caller free to choose the input amount.

The same divide-then-truncate pattern (without a zero-fee guard) recurs in the surplus-sharing logic of the same contract (`protocolShare = (surplus * SURPLUS_SHARE_BPS) / 10_000`), confirming this is a systemic, not one-off, gap.

### Impact Explanation
This is a logic/economic-invariant break: the protocol's intended fee-charging guarantee ("every order pays `protocolFeeBps`") can be deterministically bypassed by choosing an input amount under the rounding threshold. For a given `protocolFeeBps`, the exploitable range is `amount < 10_000/protocolFeeBps` raw token units. For an 18-decimal token this threshold is far below any economically meaningful transfer, but for low-decimal fee/input tokens (6-decimal stablecoins), it directly reduces the effective threshold in real value terms, and — because `placeOrder`/`fillOrder` permit repeated calls — an attacker can structure many just-under-threshold orders to move value while contributing zero cumulative protocol revenue, which is fund loss to the protocol's fee sink over time. This does not steal user funds or forge cross-chain state, but it is a genuine "logic attack" against the fee-accounting invariant the contract is supposed to enforce, unconditionally, on every fill.

### Likelihood Explanation
Reachable via the unprivileged, public `placeOrder`/order-fill entrypoints with no special role, relayer, prover, or governance involvement — any user can trivially construct an order whose input amount sits under the rounding threshold. Likelihood of triggering the zero-fee path is high (a one-line arithmetic condition); the economic upside for the attacker scales inversely with the token's decimal count and the configured `protocolFeeBps`, so it is most material on low-decimal fee tokens or low bps configurations, consistent with the original report's own caveat.

### Recommendation
In the fee-deduction loop, revert (or round up) when the computed fee is zero for a non-zero, fee-eligible input, e.g.:
```solidity
uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
if (protocolFeeBps > 0 && protocolFee == 0) revert FeeRoundsToZero();
```
Alternatively adopt round-up (`ceil`) fee math consistent with `grossUpForProtocolFee`/`divCeil` already used elsewhere in the SDK [2](#0-1)  so the on-chain contract's rounding direction matches the SDK's conservative gross-up assumption, and enforce a documented minimum order size per fee-token decimals.

### Proof of Concept
1. Governance sets `protocolFeeBps = 30` (0.3%) for a destination.
2. Attacker calls `placeOrder` with `order.inputs[0].amount = 300` (raw units) of a 6-decimal token (e.g., 0.0003 USDC).
3. In the fee loop: `protocolFee = (300 * 30) / 10_000 = 9 / 10 = 0` (integer division truncates), so `reducedAmount = 300 - 0 = 300`.
4. The order is escrowed for the full `300` with zero fee collected and no `DustCollected` emission gated on non-zero fee, `protocolFeeBps > 0` — the loop still executes, no revert occurs.
5. Repeating this pattern with just-under-threshold amounts lets an attacker move fee-eligible order volume while contributing zero protocol fee revenue, contradicting the gateway's documented "protocol retains its fee" invariant demonstrated in the passing test [3](#0-2) . [4](#0-3)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L301-332)
```text
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

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
                unchecked {
                    ++i;
                }
            }

            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L256-263)
```text
        // Verify solver received amount after fee
        assertEq(
            usdc.balanceOf(solver), solverUsdcBefore + amountAfterFee, "Solver should receive amount after protocol fee"
        );

        // Verify protocol retained its fee
        assertEq(usdc.balanceOf(address(gatewayWithFees)), expectedFee, "Gateway should retain protocol fee");
    }
```
