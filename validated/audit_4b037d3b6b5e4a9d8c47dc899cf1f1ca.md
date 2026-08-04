## Title
Malicious order creator can permanently lock filler collateral via a zero-amount escrow leg in `IntentGatewayV2.withdraw` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder` never validates that each individual `TokenInfo.amount` in `order.inputs` is non-zero — it only checks that the `inputs` array itself is non-empty. The escrowed input tokens are later paid out atomically in a loop inside `withdraw()`. If any one of the escrowed legs is a token that reverts on a zero-value `transfer` (a documented ["weird ERC-20" behavior](https://github.com/d-xo/weird-erc20#revert-on-zero-value-transfers)) and the order creator sets that leg's `amount` to `0`, the entire `withdraw()` call reverts every time it is invoked — for both the `RedeemEscrow` and `RefundEscrow` paths — permanently trapping every other (legitimately valuable) escrowed token in that order.

### Finding Description
`placeOrder` validates only array length, not amounts: [1](#0-0) 

Per-leg amounts flow straight from user input into `reducedInputs`/the commitment with no non-zero check: [2](#0-1) 

The payout path, reached from both `onAccept` (`RedeemEscrow`) and `onGetResponse`/`onPostRequestTimeout`-style refund flow (`RefundEscrow`), iterates every token in the order body and calls `transfer` unconditionally, with no zero-amount guard, and reverts the whole function (`TransferFailed`/`InsufficientNativeToken`) if any single leg's external call fails: [3](#0-2) 

This is exactly the bug class from the referenced VUSD report: a queue/loop of value transfers processes a zero-amount entry through a token that disallows zero-value transfers, and because the loop/queue is atomic, one bad entry blocks everything behind it — here, every other escrowed token amount in the *same* `withdraw()` call, for the lifetime of that order (there is no way to skip/retry a single leg; `withdraw` is only reachable via `onAccept`/`onGetResponse`, both of which run the full loop before any state is persisted).

### Impact Explanation
A malicious (or careless) order creator controls every field of `order.inputs`, including per-leg `amount`. By including a token known to revert on a zero-value transfer with `amount = 0` alongside legitimate, valuable escrowed tokens, the creator guarantees that `withdraw()` — called either to redeem the escrow to the filler (`isRefund = false`) or refund it back to the user (`isRefund = true`) — will always revert. A filler who has already delivered the corresponding outputs on the destination chain (real capital loss) can never redeem the escrowed inputs, and even the order creator's own refund path is bricked. This is a direct loss/permanent lock of escrowed bridge funds, matching the gate's "stealing or loss of funds" / "logic attacks" categories, reachable by any unprivileged user calling the public `placeOrder` entrypoint with no relayer, prover, or admin collusion required.

### Likelihood Explanation
Likelihood is limited by the practical availability of "revert-on-zero-transfer" ERC-20s as escrow-eligible assets and by rational fillers being able to inspect `order.inputs` (via the `OrderPlaced` event) before committing to fill. However, nothing in the contract prevents constructing and having such an order accepted/escrowed, and a filler or automated filling bot that doesn't specifically screen every leg for `amount == 0` combined with a non-standard token is exposed. Protocol-fee rounding (`reducedAmount = originalAmount - protocolFee`) can also independently zero out a leg at high `protocolFeeBps` without any malicious intent.

### Recommendation
In `placeOrder`, reject any `order.inputs[i].amount == 0` (and likewise for `order.outputs`/`predispatch.assets` if applicable). As defense in depth, in `withdraw()` skip the `transfer`/native-call for any leg whose `amount == 0` (mirroring the VUSD fix pattern: `if (amount > 0) { ...transfer... }`), so a single degenerate leg cannot revert the payout of the remaining legitimate escrowed value.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [ {token: WEIRD_ZERO_REVERT_TOKEN, amount: 0}, {token: USDC, amount: 1000e6} ]`, escrowing both legs.
2. A filler observes the order, delivers the requested outputs on the destination chain, and expects to redeem the escrow via the `RedeemEscrow` flow, which calls `withdraw(body, false)`.
3. Inside `withdraw`, the loop reaches the `WEIRD_ZERO_REVERT_TOKEN` leg and calls `token.call(transfer(beneficiary, 0))`; the token reverts on zero-value transfers, so `success == false` and the function reverts with `TransferFailed()` per [4](#0-3) .
4. Every subsequent call to `withdraw` for this `commitment` (redeem or refund) reverts identically — the 1000 USDC (and any tx fees in `_orders[commitment][TRANSACTION_FEES]`) are permanently stuck in the contract, and the filler's already-delivered outputs are an uncompensated loss.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L332-335)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable {
        // Validate that order has inputs
        if (order.inputs.length == 0) revert InvalidInput();

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L350-368)
```text
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

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
