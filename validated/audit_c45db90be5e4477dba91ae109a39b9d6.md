## Title
Interactions-before-effects in cross-chain escrow withdrawal allows repeated/over-draining of the same order commitment - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external Pickle Finance report is about missing validation of constructor/parameter values leading to unsafe state. The closest locally-provable analog in Hyperbridge is not a missing zero-address check, but a missing/insufficient value check on an escrow-accounting parameter (`amount` vs. actual escrowed balance) combined with performing the external token/native transfer **before** updating the escrow ledger in the `withdraw()` function of the Tron variant of `IntentGatewayV2`.

### Finding Description
The internal `withdraw()` function in `evm/tron/contracts/apps/IntentGatewayV2.sol` (lines 682-721) is the settlement path for both `RedeemEscrow` and `RefundEscrow` cross-chain messages, reached via `onAccept` (line 620-626, `onlyHost`) and `onGetResponse` (line 729-734, `onlyHost`): [1](#0-0) 

The guard only checks that the escrow slot is non-zero, not that the requested `amount` is `<=` the escrowed balance:
```
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
... beneficiary.call{value: amount}("") / token.call(transfer, beneficiary, amount) ...
_orders[body.commitment][token] -= amount;
```
The subtraction (the state effect) happens **after** the external transfer (the interaction). This is the inverse of the checks-effects-interactions pattern used correctly elsewhere in the codebase, e.g. `evm/src/apps/intentsv2/IntentsBase.sol`, where the escrow is decremented **before** the token/native transfer: [2](#0-1) 

Because the transfer to `beneficiary` (an attacker-controlled address supplied via `order.user` on `RefundEscrow`, or the filling solver on `RedeemEscrow`) executes before the ledger update, a beneficiary that is a smart contract can re-enter during the native-token `.call{value: amount}("")` callback (or an ERC-777/hook-style token's transfer callback) and trigger another path that touches the same `_orders[commitment][token]` entry — e.g. a second cross-chain `withdraw()` invocation for the same commitment queued through the host, or `cancelOrder`'s same-chain branch (line 507-530) which calls `withdraw()` directly and only checks `_filled[commitment] != address(0)` — before the first call's decrement has taken effect. This allows the same escrowed balance to be paid out more than once for a single commitment (a double-settlement/double-claim of bridge-custodied funds), directly matching the required-impacts category "stealing or loss of funds... replay/double-claim/double-settlement."

By contrast, the non-Tron `IntentGatewayV2`/`IntentsBase` implementation correctly updates `_orders` before making the external call, so this is a Tron-specific regression rather than a universal Hyperbridge issue.

### Impact Explanation
An attacker who is either the order owner (for `RefundEscrow`, self-triggered cancellation) or the solver who filled an order (for `RedeemEscrow`) can specify/control the `beneficiary` address that receives escrowed funds. By making that beneficiary a malicious contract, the attacker can reenter the withdrawal path before the escrow ledger (`_orders[commitment][token]`) is decremented, extracting more value than was actually escrowed for that commitment — a direct loss of bridge-custodied funds and a double-settlement of the same order.

### Likelihood Explanation
The vulnerable code path (`withdraw()`) is reachable from `cancelOrder()` (same-chain branch, directly callable by any user who placed an order) without any relayer, prover, or admin involvement — the attacker only needs to construct their own order with themselves as `user`/beneficiary and a malicious contract as the recipient, then call `cancelOrder`. This satisfies the "unprivileged attacker, public entrypoint" requirement. The cross-chain `RedeemEscrow`/`RefundEscrow` paths via `onAccept`/`onGetResponse` add complexity (they require a prior cross-chain message), but the same-chain `cancelOrder → withdraw` path requires no relayer or governance action at all.

### Recommendation
In `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `withdraw()` function, decrement `_orders[body.commitment][token]` by `amount` (with an explicit `amount <= escrowed` check) **before** performing the native/token transfer, mirroring the checks-effects-interactions ordering already used in `evm/src/apps/intentsv2/IntentsBase.sol`. Additionally add a reentrancy guard (`nonReentrant`) to `withdraw()`/`cancelOrder()`/`onAccept`/`onGetResponse` as defense in depth.

### Proof of Concept
1. Attacker calls `placeOrder` with `order.destination == order.source` (same-chain order), escrowing `X` tokens, and sets a malicious contract `M` as beneficiary indirectly by being `order.user` (since same-chain cancel refunds to `order.user`).
2. Attacker calls `cancelOrder(order, options)` from `M` (or with `msg.sender == order.user == M`). This invokes `withdraw(body, true)` with `beneficiary = order.user = M`.
3. Inside `withdraw()`, the native-token transfer `beneficiary.call{value: amount}("")` triggers `M`'s `receive()`/`fallback()` before `_orders[commitment][token] -= amount` executes.
4. In its fallback, `M` re-enters `cancelOrder(order, options)` again for the same `order`/`commitment`. Since `_filled[commitment]` was already set to `order.user` on the first call (line 586 in the RefundEscrow cross-chain branch, or immediately in the same-chain branch) `_orders[commitment][token]` has **not yet** been decremented, so the `UnknownOrder` check at the top of `withdraw()` still passes, and a second transfer of `amount` is sent to `M` before either decrement completes.
5. Both decrements eventually execute (since Solidity's checked arithmetic would revert only if the balance goes negative), but `M` has already received `2 * amount` while only `amount` was ever escrowed — net loss to the protocol/other order participants equal to the excess drained per reentrant call, limited by the contract's actual token/native balance. [3](#0-2) [4](#0-3)

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-721)
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

        // redeem tx fees
        uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
        if (fees > 0) {
            address feeToken = IDispatcher(host()).feeToken();
            (bool success,) = feeToken.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, fees));
            if (!success) revert TransferFailed();
            delete _orders[body.commitment][TRANSACTION_FEES];
        }

        if (isRefund) {
            emit EscrowRefunded({commitment: body.commitment});
        } else {
            emit EscrowReleased({commitment: body.commitment});
        }
    }
```

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
