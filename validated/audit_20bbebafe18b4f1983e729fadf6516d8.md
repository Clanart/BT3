## Title
Unchecked ERC-20 return value in Tron `IntentGatewayV2.withdraw()` lets a malicious order creator steal solver output tokens without paying escrowed input — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron build of `IntentGatewayV2` settles intent orders (fills, cancellations, refunds) by moving escrowed tokens through a raw low-level `.call` to the ERC-20 `transfer` selector instead of using `SafeERC20`, even though `SafeERC20` is imported and used elsewhere in the same contract (`using SafeERC20 for IERC20;`). The settlement code only checks that the `.call` did not revert — it never inspects the returned boolean. A non-reverting ERC-20 that returns `false` on a failed transfer therefore lets `withdraw()` mark the order as settled and emit `EscrowReleased`/`EscrowRefunded` while the tokens never actually move to the beneficiary.

### Finding Description
In `withdraw()`: [1](#0-0) 

Both the native-token branch and the ERC-20 branch update accounting (`_filled[body.commitment] = beneficiary;` and `_orders[body.commitment][token] -= amount;`) and only guard against the call *reverting*, not against a token contract that returns `false`: [2](#0-1) 

This is the settlement path invoked for every fill and cancellation outcome — cross-chain fill releases (`onAccept` → `withdraw`) and source-chain cancel refunds (`onGetResponse` → `withdraw`): [3](#0-2) 

Since `order.inputs[i].token` is an arbitrary address chosen by the order creator at `placeOrder()` time, an attacker can escrow a custom ERC-20 whose `transfer()` implementation returns `false` (without reverting) specifically when called to pay out to the solver, while behaving normally (or even reverting `false`-conditionally, e.g. only when the caller is the gateway paying a *different* address than the deployer) for the initial `transferFrom` deposit into escrow. A solver who is lured into filling such an order delivers the real, correct output tokens to the order's `beneficiary` on the destination chain and dispatches the `RedeemEscrow` message. When that message lands and `withdraw()` executes on the source chain, the escrow-release `.call` to the malicious token's `transfer()` returns `false`, `success` is still `true` at the Solidity level check on the call itself is separately checked — but the code only reverts if the low-level call itself reverts, not if it returns `false`. The result: `_filled` is set, `_orders[...][token]` is decremented, `EscrowReleased` is emitted, but the solver's tokens were never moved. The solver has already paid the real output tokens and receives nothing in return, and there is no retry path because the order is now marked filled/settled.

### Impact Explanation
This is direct loss of funds for an unprivileged, honest counterparty (the solver) triggered entirely by an unprivileged attacker (the order creator choosing the input token address). No relayer, prover, admin, or governance compromise is required — only the ability to deploy an arbitrary ERC-20 and place an order with it, both of which are ordinary, permissionless user actions in this protocol.

### Likelihood Explanation
Medium-to-high: exploitation only requires deploying a standard-looking ERC-20 with a crafted `transfer()` that returns `false` under attacker-chosen conditions, then placing a normal order and waiting for any solver to fill it. This does not require timing races, front-running, or privileged roles — it's a straightforward, repeatable griefing/theft primitive against any solver that fills orders backed by attacker-supplied tokens.

### Recommendation
Replace all raw `.call(abi.encodeWithSelector(IERC20.transfer/transferFrom.selector, ...))` token movements in `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()` (and the equivalent `sweepDust`/fee-release paths) with `SafeERC20.safeTransfer`/`safeTransferFrom`, which already validate the boolean return value in addition to call success, consistent with the non-Tron `IntentGatewayV2.sol`/`IntentsBase.sol` implementations that use `SafeERC20` throughout.

### Proof of Concept
1. Deployer creates `EvilToken` implementing `IERC20` where `transfer(to, amount)` returns `false` (no revert) whenever `to != order.user` (i.e., whenever paying out to someone other than the attacker).
2. Attacker places a same-chain or cross-chain order with `order.inputs[0].token = address(EvilToken)`, escrowing `EvilToken` via a normal `transferFrom` from the attacker to the gateway (succeeds, since `to == gateway`, not restricted).
3. A solver fills the order, delivering genuine output tokens to `order.output.beneficiary` and triggering the settlement message.
4. On settlement, `withdraw()` calls `EvilToken.transfer(solver, amount)`, which returns `false` but does not revert; `success` from the low-level `.call` is `true` (the call executed without reverting), so the code proceeds, decrements `_orders`, sets `_filled`, and emits `EscrowReleased`.
5. The solver never receives the escrowed `EvilToken`, yet already paid the legitimate output tokens on the destination chain — a net theft of the solver's funds with no recovery mechanism.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L729-735)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
}
```
