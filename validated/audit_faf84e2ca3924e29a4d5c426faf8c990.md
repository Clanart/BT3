## Analysis

The report's core broken invariant: an escrow-release function transfers **caller/message-supplied amount fields** while only checking that *some* escrow record exists for the given key, without verifying the amount matches (or does not exceed) what was actually recorded as escrowed. In Hyperbridge, the closest local analog is the escrow accounting mismatch between `placeOrder` and `withdraw` in `IntentGatewayV2`.

### Title
Order escrow permanently locked due to fee-reduced escrow accounting vs. unreduced withdrawal amount mismatch - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder` records escrow using the **protocol-fee-reduced** amount (`reducedInputs[i].amount`), but `cancelOrder`'s same-chain path builds the `WithdrawalRequest` using the **original, unreduced** `order.inputs[i].amount`. `withdraw()` only validates that the escrow slot is non-zero (`_orders[commitment][token] == 0` → revert), never that the requested `amount` is `<=` the recorded escrow. Because Solidity `^0.8.24` reverts on underflow, `_orders[body.commitment][token] -= amount` always reverts when `amount` (unreduced) exceeds the recorded escrow (reduced), permanently blocking release of the escrowed principal for any order that incurred a non-zero protocol fee.

### Finding Description
In `placeOrder`, when `protocolFeeBps > 0`, the escrow bookkeeping stores the fee-reduced amount: [1](#0-0) [2](#0-1) [3](#0-2) 

In both the predispatch and direct-escrow branches, `_orders[commitment][token] += reducedInputs[i].amount` — the post-fee amount — is what's tracked as "owed" per commitment/token.

`cancelOrder`'s same-chain branch, however, constructs the withdrawal body from the **original** `order.inputs` (pre-fee amounts), not `reducedInputs`: [4](#0-3) 

`withdraw()` then uses `body.tokens[i].amount` directly for the transfer and for decrementing the ledger, gating only on non-zero existence rather than a sufficiency check: [5](#0-4) 

Since `amount` (unreduced, larger) is subtracted from `_orders[body.commitment][token]` (reduced, smaller), the subtraction underflows and Solidity's built-in checked arithmetic reverts the entire transaction — including the token transfer that preceded it in the same call. This makes `cancelOrder()` on the same chain permanently unusable for any order that had `protocolFeeBps > 0`, locking the user's escrowed principal in the contract with no other public path to release it (the only other releases, `RedeemEscrow`/`RefundEscrow` via `onAccept`, and `onGetResponse`, are reachable only through cross-chain messages from the paired instance/hyperbridge and are outside this local, permissionless call path).

This directly parallels the report's flaw: the withdrawal path is **not strictly bound** to the amount actually escrowed/verified — it only checks presence, not correctness/sufficiency of the amount being moved.

### Impact Explanation
This is a "loss of funds" class bug: any user placing an order under a chain/destination configuration with `protocolFeeBps > 0` who later needs to cancel a same-chain order via the permissionless `cancelOrder()` entrypoint will find their principal and reserved fee tokens permanently stuck in the `IntentGatewayV2` contract, since the only local cancellation path unconditionally reverts. This matches the bounty's "stealing or loss of funds" and "order escrow ... must move exactly once and only to the rightful beneficiary and amount" pivot — here it never moves at all.

### Likelihood Explanation
High likelihood: this is triggered by the order owner themselves calling a fully public, unprivileged function (`cancelOrder`) on their own order — no relayer, prover, governance actor, or malicious peer is required. It fires deterministically whenever `_destinationProtocolFees[...]` or `_params.protocolFeeBps` is non-zero, which is an expected, governance-configured production state, not an edge case.

### Recommendation
- Store (or recompute) the exact amount that was escrowed per `(commitment, token)` and always release **that** amount, never a caller/order-supplied value taken at face value.
- In `withdraw()`, replace the `== 0` existence check with an explicit sufficiency check (`amount <= _orders[commitment][token]`) and reject the record if it doesn't match, instead of relying on unchecked-arithmetic revert.
- Ensure `cancelOrder`'s same-chain path builds `WithdrawalRequest.tokens` from the same `reducedInputs` used to populate escrow, not from `order.inputs`.

### Proof of Concept
1. Governance sets `protocolFeeBps > 0` (or a destination-specific fee) for a chain.
2. User calls `placeOrder` with a same-chain order (`order.source == order.destination`) and `order.inputs = [{token: T, amount: 1000}]`. Escrow recorded: `_orders[commitment][T] = 1000 - fee` (e.g. 990), while `order.inputs[0].amount` remains 1000.
3. User calls `cancelOrder(order, options)`. Since `isSameChain` is true and `order.user == msg.sender`, it builds `WithdrawalRequest({commitment, tokens: order.inputs /* amount=1000 */, beneficiary: order.user})` and calls `withdraw(body, true)`.
4. In `withdraw`: `_orders[commitment][T] == 990`, non-zero so passes the `UnknownOrder` check; token transfer of `1000` is attempted; then `_orders[commitment][T] -= 1000` → `990 - 1000` underflows → reverts.
5. The entire `cancelOrder` transaction reverts every time it is retried with the same order data — the 990 tokens (and any escrowed fee token) remain locked in the contract with no available code path to release them locally. [6](#0-5) [5](#0-4)

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L434-435)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L456-457)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;
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
