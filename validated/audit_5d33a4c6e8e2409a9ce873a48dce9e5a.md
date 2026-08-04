## Title
Silent loss of escrowed funds due to missing code-length check on `IntentGatewayV2.withdraw()` token transfers - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2` (Tron variant) redeems/refunds escrowed order funds through a raw low-level `.call()` to the escrowed ERC20 token instead of using `SafeERC20.safeTransfer` (which is used everywhere else in the same contract for deposits). A low-level `.call()` to an address with no deployed code returns `success = true` with empty return data in the EVM — it never reverts. `withdraw()` treats that trivial success as proof of a real transfer, permanently marking the order as filled/redeemed and decrementing escrow accounting, even though no tokens were actually moved. This is the same broken invariant as the referenced report (transfer errors must never be silently accepted as success), except here the failure mode is worse: instead of a revert-based DoS, it is a silent, permanent loss of the beneficiary's escrowed funds.

### Finding Description
`placeOrder()` escrows input tokens using `SafeERC20.safeTransferFrom`, which internally verifies the target has code and correctly bubbles up failures: [1](#0-0) 

The redemption/refund path, `withdraw()`, does the opposite — it manually encodes the `IERC20.transfer` selector and dispatches it via a raw `.call()`, checking only the boolean success flag returned by the low-level call: [2](#0-1) 

The transaction-fee redemption a few lines below repeats the same pattern: [3](#0-2) 

Per EVM semantics, `address.call(data)` against an address with **no contract code** does not revert — it returns `(true, "")`. `if (!success) revert TransferFailed();` therefore never fires for a codeless `token`, and execution falls through to finalize the order:
- `_orders[body.commitment][token] -= amount;` (escrow accounting is decremented as if paid out)
- `_filled[body.commitment] = beneficiary;` (order permanently marked as settled)

`withdraw()` is reached from the cross-chain, protocol-driven `RedeemEscrow`/`RefundEscrow` message path (`onAccept`, authenticated via `authenticate()`), and from the same-chain `cancelOrder()` path: [4](#0-3) [5](#0-4) 

The escrowed `token` address is fixed at `placeOrder()` time and is the same address the contract actually holds a real ERC20 balance for at deposit time (deposit succeeds only because `safeTransferFrom` requires code to exist then). The vulnerability window is between deposit and the eventual `withdraw()` call: if that specific token contract becomes codeless in the interim (e.g. `SELFDESTRUCT`, a destroyed/undeployed proxy implementation, or any other mechanism that zeroes `extcodesize`), the subsequent `withdraw()` silently "succeeds" without transferring anything, and the order/escrow bookkeeping is finalized irreversibly. There is no other public entry point that can retry the transfer for that commitment once `_filled` is set — `cancelOrder()` itself refuses to act on an already-filled order (`if (_filled[commitment] != address(0)) revert Filled();`), so the funds are unrecoverable.

### Impact Explanation
This causes a direct, permanent loss of the beneficiary's escrowed funds and false-success finalization of the order/refund state — the exact `Fund loss / lock` and `false state acceptance` classes called out in the Hyperbridge Impact Gate. The failure is silent (no revert, no error surfaced to callers/relayers), so it is strictly worse than the original reported bug class (which at least DoS'd/reverted): here the protocol state (order filled, escrow zeroed) diverges permanently from reality (beneficiary got nothing), and there is no remediation path once `_filled[commitment]` is set.

### Likelihood Explanation
Reaching this requires the escrowed token to lose its code between deposit and redemption/refund — an edge-case external condition, exactly analogous to the original report's `codesize == 0` scenario. It does not require a malicious peer, relayer, or admin: the withdraw call itself is a normal part of the protocol's authenticated cross-chain settlement flow (or the user's own `cancelOrder`), and any token whose implementation can lose its code (self-destructing tokens, proxy patterns whose implementation is destroyed, metamorphic contracts) triggers it without any additional privilege.

### Recommendation
Mirror the fix from the source report: gate the low-level transfer calls in `withdraw()` (and the dust-sweep/fee paths using the same pattern) on `token.code.length > 0` before attempting the call, and revert (or route to a recoverable pending-withdrawal state) if the token is codeless, instead of treating the trivial `true` return as a successful transfer. Preferably, replace the manual `.call(abi.encodeWithSelector(IERC20.transfer.selector, ...))` pattern in `withdraw()` with `SafeERC20.safeTransfer`, consistent with the deposit path, since `SafeERC20` already performs this contract-existence check.

### Proof of Concept
1. User calls `placeOrder()` escrowing `amount` of `TokenX` (a legitimate contract with code at deposit time) via `safeTransferFrom`; `_orders[commitment][TokenX] = amount`.
2. Before the corresponding `RedeemEscrow`/`RefundEscrow` message is delivered (or before `cancelOrder` is invoked for a same-chain order), `TokenX` becomes codeless (e.g. it self-destructs, or is a proxy whose implementation is destroyed).
3. The protocol delivers the `RedeemEscrow`/`RefundEscrow` message (or the user calls `cancelOrder`), reaching `withdraw()`.
4. In `withdraw()`: `token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount))` targets the now-codeless `TokenX` address; the EVM/TVM returns `(success = true, "")` without executing any transfer logic.
5. `if (!success) revert TransferFailed();` does not trigger. `_orders[commitment][TokenX] -= amount;` and `_filled[commitment] = beneficiary;` execute, finalizing the order.
6. The beneficiary receives zero tokens; the order is permanently marked filled; no further call can be made to retry or refund the commitment — the escrowed value is lost.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L452-454)
```text
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L507-530)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable {
        bytes32 commitment = keccak256(abi.encode(order));

        // order has already been filled
        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L707-714)
```text
        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }
```
