## Title
Duplicate input-token accumulation in Tron `IntentGatewayV2.placeOrder` allows over-release of escrow to a solver that fills only one leg of a multi-asset order - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron variant of `IntentGatewayV2.placeOrder` credits escrow with `_orders[commitment][token] += reducedInputs[i].amount;` for every input index, with no check that `token` is unique across `order.inputs`. The canonical EVM contract (`evm/src/apps/IntentGatewayV2.sol`) explicitly guards against this: `if (_orders[commitment][token] != 0) revert InvalidInput();` before writing escrow [1](#0-0) . The Tron contract has no such guard [2](#0-1) .

Because escrow is keyed only by `commitment => token address` (not by input index), an order whose `inputs` array lists the same token twice (once per output "leg") causes the two legs' escrow amounts to be summed into a single bucket. On the fill/withdraw path, this same key is what actually gets paid out.

### Finding Description
`_fillSameChain` in `IntrinsicIntents.sol` (shared same-chain fill logic) computes, per output-asset index `i`, the escrow amount to release to the solver:

```solidity
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [3](#0-2) 

When a leg is fully filled (`amountFilled == totalRequired`), the code does not release the proportional amount tied to that leg's `inputs[i]` — it releases **whatever is currently sitting in `_orders[commitment][token]`** for that token address. On the main EVM contract, this is safe because `placeOrder` rejects duplicate input tokens, so each token address in `_orders[commitment][...]` corresponds 1:1 to a single input leg. On Tron, `placeOrder` has no such rejection and instead **accumulates** (`+=`) escrow across all input entries sharing the same token address [4](#0-3) .

Consequently, if a user (or an order crafted/relayed on their behalf) places a multi-leg order where two different output legs are both backed by the *same* input token (e.g., `inputs = [{USDC, 500}, {USDC, 500}]` funding `outputs = [{DAI, small}, {ETH, large}]`), `_orders[commitment][USDC]` becomes `1000`, not `500` per leg. A solver who fully satisfies only the small/cheap leg (`outputs[0]`) triggers `amountFilled == totalRequired` for that leg and the contract reads and pays out the **entire accumulated USDC bucket (1000)** via `_withdraw`, releasing escrow that was meant to back the *other*, unfilled leg. The remaining leg's output requirement (ETH) is never delivered to the beneficiary, yet its backing collateral has already been drained to the solver.

This is structurally the same broken invariant as the reported liquidation bug: a value that should be strictly bound to the specific unit being settled (specific loan/specific order leg) is instead read from a shared/aggregated pool, letting an attacker trigger release of funds disproportionate to what they actually delivered.

### Impact Explanation
A solver can obtain the full escrowed value of a multi-leg, same-token-input order by fulfilling only the cheapest leg, while the user's other, unfilled leg's backing collateral is drained. This is direct fund loss/wrong-beneficiary payout from the intent-settlement escrow, matching the bounty's "stealing or loss of funds" / "transaction manipulation" / "wrong beneficiary or amount" categories, reachable by any unprivileged solver calling the public `fillOrder`/`_fillSameChain` path with no relayer, prover, or governance involvement.

### Likelihood Explanation
Requires that an order actually contains two input entries with the same token address feeding different output legs — this is a valid, unrestricted construction under the Tron contract's `placeOrder` (no duplicate check exists), so a malicious "user" side (or a solver colluding with the order placer) can deliberately shape such an order and then, as solver, fill only the cheap leg to drain the shared escrow. This makes it a self-contained, easily triggerable exploit rather than requiring any privileged or off-chain-compromised actor.

### Recommendation
Port the EVM guard to the Tron contract: reject duplicate input tokens in `placeOrder` (`if (_orders[commitment][token] != 0) revert InvalidInput();`), or, more robustly, track escrow per input index (not per token address) so that per-leg release in `_fillSameChain`/`withdraw` cannot cross-contaminate between legs even if duplicate tokens are ever allowed.

### Proof of Concept
1. User (Tron) calls `placeOrder` with `inputs = [{token: USDC, amount: 500}, {token: USDC, amount: 500}]` and `output.assets = [{token: DAI, amount: 1}, {token: WETH, amount: 10}]` (leg 0 cheap, leg 1 expensive). `_orders[commitment][USDC]` becomes `1000` due to the `+=` accumulation at [5](#0-4) .
2. A solver calls `fillOrder` providing only `outputs[0] = {DAI, 1}` (satisfies leg 0 fully) and `outputs[1] = {WETH, 0}` (leaves leg 1 unfilled) — allowed since a `solverAmount == 0` leg is simply skipped (`isFullyFilled` becomes `false`, order not marked fully filled, but the per-leg withdrawal for leg 0 still executes independently) [6](#0-5) .
3. For leg 0, `amountFilled == totalRequired` is true, so `escrowedAmount = _orders[commitment][USDC] = 1000` is placed into `escrowedInputs[0]` and passed to `_withdraw`, transferring the full 1000 USDC to the solver for having delivered only 1 DAI [7](#0-6) .
4. The remaining leg (10 WETH) is still owed to the user, but its backing USDC escrow has already been fully paid out to the solver, leaving the order under-collateralized.

**Uncertainty**: I could not fully trace whether the Tron contract's `_fillSameChain`/withdraw call sites are byte-identical to the shared `IntrinsicIntents.sol` used by the main EVM build, or whether Tron uses its own divergent fill implementation (the file is very large and I only reviewed `placeOrder`, `cancelOrder`, `onAccept`, and `withdraw`). If Tron's fill function independently re-derives per-leg escrow rather than reusing `IntrinsicIntents.sol`, the exploit path would need re-verification against that specific function — I recommend a follow-up read of the Tron contract's fill-order logic (likely between the `cancelOrder`/`onAccept` region shown and wherever `_fillSameChain`-equivalent logic lives) to confirm the exact code path before treating this as fully proven for Tron.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L431-463)
```text
                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }

            // Execute transfer calls from call dispatcher
            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-79)
```text
            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```
