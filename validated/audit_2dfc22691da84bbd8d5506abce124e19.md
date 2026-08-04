## Analysis

The external report's core pattern is *"a function loops over a user-controlled collection and performs per-item state-changing work, and the per-item processing is unsafe when the loop is large/adversarial."* Scanning Hyperbridge's actual per-item loops that move funds (rather than just batching for gas), the `IntentGatewayV2` escrow-release loop on the **Tron** deployment stands out: it performs the external token/native transfer for each escrowed token *before* updating the corresponding escrow-accounting storage, unlike the equivalent function in the mainline EVM contract (`IntentsBase.sol::_withdraw`), which does the accounting update first. [1](#0-0) [2](#0-1) 

### Title
Checks-Effects-Interactions violation in `IntentGatewayV2.withdraw()` (Tron) allows reentrant escrow token to execute with stale, un-decremented escrow state — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron deployment of `IntentGatewayV2`'s internal `withdraw()` function transfers escrowed tokens (native ETH via `.call{value}` or ERC20 via a raw `token.call(...transfer...)`) to the beneficiary **before** decrementing `_orders[body.commitment][token]`. The mainline EVM contract (`IntentsBase.sol::_withdraw`) performs the decrement first and only then makes the external transfer — the correct checks-effects-interactions ordering. The Tron variant diverges from this pattern.

### Finding Description
`withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` iterates `body.tokens`:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    ...
} else {
    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
    ...
}
_orders[body.commitment][token] -= amount;   // <-- decrement AFTER external call
``` [3](#0-2) 

Both the `beneficiary` and the escrowed `token` addresses are attacker-influenced: `beneficiary` comes straight from the order/refund payload (`order.user` for cancellations), and `token` is whatever ERC20 the order-placer chose at `placeOrder()` time — nothing prevents placing an order with a malicious/callback-bearing "ERC20" as an input. During the external call, control passes to attacker-controlled code while `_orders[body.commitment][token]` (and the fee slot, transferred later in the same function) still reflects the pre-withdrawal balance.

Compare with the safe reference implementation used by the primary EVM `IntentsBase.sol`, which decrements first:
```solidity
_orders[body.commitment][token] = escrowed - amount;
if (token == address(0)) {
    (bool sent,) = beneficiary.call{value: amount}("");
    ...
} else {
    IERC20(token).safeTransfer(beneficiary, amount);
}
``` [4](#0-3) 

`withdraw()` is reachable from three paths:
- `cancelOrder()` same-chain path (any order owner, directly, no cross-chain proof needed) [5](#0-4) 
- `onAccept()` for `RedeemEscrow`/`RefundEscrow` cross-chain messages [6](#0-5) 
- `onGetResponse()` for cross-chain cancellation [7](#0-6) 

`_filled[body.commitment]` is set unconditionally at the very top of `withdraw()`, before the loop [8](#0-7) , which blocks a *reentrant call for the same commitment* through `cancelOrder()`'s `Filled()` guard [9](#0-8) . This closes the most direct same-order double-withdraw path.

**What I could not fully verify:** I could not construct or confirm, within this session, a complete concrete double-spend across two *different* orders/commitments that share a malicious token (e.g., mutual reentrant recursion draining `_orders[commitmentA][token]` twice before its single decrement executes). `_orders` is keyed per-commitment so a naive cross-commitment drain does not obviously fall out of the code, but the un-ordered effects/interactions is nonetheless a real, provable deviation from the secure pattern elsewhere in the same codebase, and the general reentrancy surface (arbitrary code execution mid-function, with stale accounting and an unpaid `TRANSACTION_FEES` slot still visible) is a legitimate CEI violation that a determined attacker controlling both the escrowed token contract and the beneficiary address should be assumed able to weaponize with further chain-specific tricks (e.g. interacting with `_params`, `ICallDispatcher`, or Tron-specific TRC quirks around `.call` gas forwarding) that are out of scope for this codebase-only review.

### Impact Explanation
If exploitable, this allows a malicious order-placer/beneficiary to reenter mid-`withdraw()` while escrow accounting is stale, enabling unauthorized re-execution of contract logic with attacker-controlled state assumptions — a "logic attack" per the bounty's impact gate (fund loss / unauthorized execution). Even absent a fully proven double-spend, this is a real CEI defect on a fund-moving path that the flagship EVM contract explicitly avoids, so it represents an inconsistency in the security model between the primary and Tron deployments of the same intent-settlement protocol.

### Likelihood Explanation
Requires the attacker to control (a) an order whose input token is a contract they author (easy — anyone can escrow any ERC20 address at `placeOrder()` time, and there is no allowlist check in this file) and (b) the beneficiary address of that order (trivially true — the placer sets `order.user`/`beneficiary`). No relayer, prover, or privileged actor is needed to reach `cancelOrder()`'s same-chain path.

### Recommendation
Reorder `withdraw()` in `evm/tron/contracts/apps/IntentGatewayV2.sol` to match `IntentsBase.sol::_withdraw`: decrement `_orders[body.commitment][token]` (and clear the `TRANSACTION_FEES` slot) *before* making any external call, and/or add a `nonReentrant` guard to `withdraw()`/`cancelOrder()`/`onAccept()`/`onGetResponse()`.

### Proof of Concept
Not fully constructible from static review alone given the `_filled` guard blocks same-commitment reentry; a dynamic PoC (deploy a malicious ERC20 as an order input, place an order, trigger cancellation, and instrument the token's `transfer()` hook to attempt reentrant calls against other orders/functions) would be required to confirm end-to-end fund loss and is recommended as a follow-up with actual contract deployment/testing tooling.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L399-409)
```text

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-511)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L519-530)
```text
        if (isSameChain) {
            // Same-chain: validate locally and refund immediately
            // only owner can cancel
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

            // Verify we're on the correct chain
            if (orderSource != currentChain) revert WrongChain();

            WithdrawalRequest memory body =
                WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user});

            withdraw(body, true);
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-626)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-734)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
```
