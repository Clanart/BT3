## Analysis

The DBR bug's core broken invariant: a per-unit fee/interest computed as `(numerator * rate) / large_denominator` truncates to zero when the numerator is kept small, letting an actor split one large obligation into many small ones and pay **zero** aggregate fee instead of the intended proportional amount.

The closest local analog with the same broken invariant, reachable by an unprivileged attacker, and touching protocol revenue on a live money-moving path is `IntentGatewayV2.placeOrder`'s protocol-fee computation.

### Title
Protocol fee on intent orders truncates to zero for amounts below the bps threshold, letting users split orders to escrow 100% of input for free - (File: evm/src/apps/IntentGatewayV2.sol)

### Summary
`placeOrder` computes the protocol fee as `protocolFee = (originalAmount * protocolFeeBps) / 10_000` [1](#0-0) . This is integer division with a fixed 10,000 denominator; whenever `originalAmount * protocolFeeBps < 10_000`, `protocolFee` rounds down to `0`, and the branch simply skips emitting `DustCollected` and escrows the full `originalAmount` with no fee deducted. There is no minimum-fee floor and no minimum-order-size check (`InvalidInput` only guards `amount == 0`).

### Finding Description
For a configured `protocolFeeBps` (governance-set globally via `_params.protocolFeeBps` or per-destination via `_destinationProtocolFees`), the fee-free threshold is:

```
amount_max_free = ceil(10_000 / protocolFeeBps) - 1
```

Any `placeOrder` call whose per-token input amount is below this threshold pays **zero** protocol fee, identical in mechanism to the DBR report's `debt_max = 365 days / 12s - 1`. An attacker can decompose one large intent into `N = totalAmount / amount_max_free` separate `placeOrder` calls, each individually under the rounding floor, and escrow the entire notional while paying no protocol fee at all — the same "split into many small transactions to dodge the truncating fee" primitive as the DBR PoC, just applied to bps-based fee math instead of time-based interest accrual. This is a real, provable code path in production settlement logic (`evm/src/apps/IntentGatewayV2.sol`), triggerable by any unprivileged user with no relayer, prover, or admin involvement.

### Impact Explanation
Every order that dodges the fee is dust the protocol treasury never collects — a direct, permanent, unauthorized reduction of protocol revenue on the intent-settlement fee path, with the escrowed/settled amount and beneficiary otherwise unaffected (funds still move correctly to the solver/beneficiary). This falls under "logic attacks" against fee/dust accounting rather than fund theft from a counterparty, so it should be scoped at low/informational severity, mirroring the original C4 finding's own "Medium at best, arguably Low/QA" classification. The economic significance scales with `protocolFeeBps` and token decimals: for low-decimal or low-bps configurations (e.g. `protocolFeeBps = 1`), the threshold `amount_max_free = 9999` units is easily large relative to per-tx gas cost for higher-value/low-decimal tokens (e.g. WBTC-style 8-decimal assets), making the attack more attractive exactly as the original report notes for low-decimal, high-value tokens.

### Likelihood Explanation
Likelihood is directly a function of gas cost vs. avoided fee, exactly as the original report concluded ("hardly imaginable... economically feasible at the moment" for the base case, but risk increases for higher-bps configs or low-decimal/high-value tokens, and governance can raise `protocolFeeBps` or add destination overrides without redeploying, per `_destinationProtocolFees`). No malicious relayer, prover, or governance actor is required — a single ordinary user wallet suffices.

### Recommendation
- Enforce a minimum order size per token (or a minimum protocol fee, e.g. `max(protocolFee, minFeeFloor)`) so `placeOrder` reverts or charges at least 1 unit of fee whenever `protocolFeeBps > 0` and `originalAmount > 0`.
- Alternatively, round the fee computation up (`protocolFee = (originalAmount * protocolFeeBps + 9_999) / 10_000`) rather than down, eliminating the free-of-charge window entirely.
- Document and re-evaluate the acceptable `protocolFeeBps` / token-decimal combinations per deployment, since the free-of-charge window scales inversely with `protocolFeeBps`.

### Proof of Concept
Given `protocolFeeBps = 1` (0.01%), the fee-free threshold is `amount < 10_000` base units:

```solidity
// Splitting a 999,900-unit order into 100 orders of 9,999 units each:
for (uint i = 0; i < 100; i++) {
    TokenInfo[] memory inputs = new TokenInfo[](1);
    inputs[0] = TokenInfo({token: usdcAsBytes32, amount: 9_999}); // just under 10_000/1
    Order memory order = _buildOrder(inputs, ...);
    intentGateway.placeOrder(order, bytes32(0));
    // protocolFee = (9_999 * 1) / 10_000 == 0  -> no DustCollected, full 9_999 escrowed
}
// Total escrowed: 999,900 units, total protocol fee collected: 0
// A single placeOrder(999_900) would have paid protocolFee = 99 units.
```

This mirrors the DBR PoC's loop of repeated small-amount calls (`accrueDueTokens` in the original vs. `placeOrder` here) that each individually round the fee/interest term to zero via truncating integer division. [2](#0-1)

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L300-331)
```text
        // Phase 2: Compute protocol fees and commitment from actual received amounts.
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
