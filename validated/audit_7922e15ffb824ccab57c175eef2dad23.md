## Analysis

The external Rubicon report's core bug class is: **a validation check that exists in one code path is missing from a structurally parallel/duplicate code path**, allowing "dust" inputs to slip through unvalidated. Hyperbridge has a direct, provable analog to this in the intent-settlement escrow logic of the Tron variant of `IntentGatewayV2`.

### The two parallel paths

`placeOrder` in `evm/tron/contracts/apps/IntentGatewayV2.sol` has two mutually-exclusive branches depending on whether `order.predispatch` calldata is present:

- **Non-predispatch branch** (the "else"): explicitly guards every input with `if (order.inputs[i].amount == 0) revert InvalidInput();` before transferring/escrowing. [1](#0-0) 

- **Predispatch branch** (the "if"): the sibling loop over `order.inputs` that sweeps balances back from the `CallDispatcher` and books escrow/dust has **no such zero-amount check** at all. [2](#0-1) 

The canonical (non-Tron) EVM implementation of the same function *does* carry the zero-amount check in both branches — confirming it is a deliberate, expected invariant that the Tron fork dropped in one branch: [3](#0-2) [4](#0-3) 

### Why this matters

In the predispatch branch, for each input `i` the contract computes `balance = IERC20(token).balanceOf(dispatcher)` (whatever the predispatch call swapped/produced) and only checks `balance < requiredAmount` before sweeping. It then computes `dust = balance - requiredAmount` and credits escrow with `reducedInputs[i].amount` (derived from `requiredAmount`): [5](#0-4) 

If `requiredAmount` (`order.inputs[i].amount`) is `0` — which is never rejected on this path — then `dust = balance - 0 = balance`: the **entire** post-predispatch-swap balance for that leg is emitted as `DustCollected` (i.e., swept to protocol dust) and `_orders[commitment][token]` is incremented by `0`. Nothing is escrowed for that input leg even though real value passed through the `CallDispatcher`. The commitment hash is computed over an order containing a `0`-amount input, so this state is internally "valid" and unrecoverable — no refund path restores the swept balance to the order.

<br>

### Title
Missing zero-amount validation on predispatch input leg lets escrowed order value be misclassified and lost as dust - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder`'s predispatch branch omits the `order.inputs[i].amount == 0` guard that both the non-predispatch branch of the same function and the canonical EVM `IntentGatewayV2.placeOrder` enforce, allowing zero-amount input legs to be silently swept into `DustCollected` rather than escrowed.

### Finding Description
`placeOrder` builds `order.inputs` escrow bookkeeping differently depending on whether predispatch calldata is attached. The non-predispatch path validates `order.inputs[i].amount != 0` per leg before transfer. The predispatch path skips this validation and instead derives the swept amount from `IERC20(token).balanceOf(dispatcher)`, treating anything above `requiredAmount` as dust. With `requiredAmount == 0` allowed, 100% of the dispatcher's post-swap balance for that leg is emitted as `DustCollected` and zero is credited to `_orders[commitment][token]`, even though the predispatch call (e.g. a Uniswap swap funded by the user's escrowed predispatch assets) produced real value.

### Impact Explanation
This breaks the "funds move exactly once to the rightful beneficiary" invariant for order escrow: value that a user routed through predispatch calldata into the gateway is misattributed as protocol dust instead of being escrowed against the order, leaving the depositor with a `0`-value escrow leg for tokens they funded and permanently unable to reclaim them via the normal cancel/refund/fill flow (which only ever reads `_orders[commitment][token]`).

### Likelihood Explanation
Any unprivileged user constructing an `Order` with `predispatch.call`/`predispatch.assets` set and any `order.inputs[i].amount == 0` triggers this on every call — no relayer, prover, or governance action required, and no special chain conditions are needed beyond deploying/using the Tron `IntentGatewayV2`.

### Recommendation
Add the same guard used in the non-predispatch branch and in the canonical EVM contract to the predispatch loop:
```solidity
for (uint256 i; i < inputsLen;) {
    if (order.inputs[i].amount == 0) revert InvalidInput();
    address token = address(uint160(uint256(order.inputs[i].token)));
    uint256 requiredAmount = order.inputs[i].amount;
    ...
}
```

### Proof of Concept
1. User calls `placeOrder` with `order.predispatch.call` and `order.predispatch.assets` populated (e.g., swap ETH→DAI via `CallDispatcher`), and `order.inputs[0] = {token: DAI, amount: 0}`.
2. The predispatch loop transfers ETH to the dispatcher and executes the swap; the dispatcher now holds `balance` DAI.
3. In the sweep loop, `requiredAmount = 0`, so `balance < requiredAmount` is always false — no revert.
4. `dust = balance - 0 = balance` → `DustCollected(DAI, balance)` is emitted; `_orders[commitment][DAI] += reducedInputs[0].amount` adds `0`.
5. The order's commitment is stored with a `0`-amount DAI input; the user's DAI is now sitting in the gateway as unrecoverable "dust" rather than escrowed order value. [2](#0-1) [1](#0-0) [6](#0-5)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L411-440)
```text
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-454)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L230-256)
```text
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L282-283)
```text
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
```
