## Title
Tron `IntentGatewayV2.cancelOrder`/`withdraw` sends the pre-fee escrow amount instead of the actually-escrowed (post-fee) amount, causing escrow accounting to underflow-revert and permanently locking user funds on any fee-bearing same-chain order — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron fork of `IntentGatewayV2` reintroduces the exact "external call before state finalization" callback pattern that the mainline EVM contracts (`evm/src/apps/intentsv2/IntrinsicIntents.sol` / `IntentsBase.sol`) were explicitly patched against, as documented by `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`. In the Tron variant, `withdraw()` transfers tokens to `beneficiary` via a raw `.call` **before** decrementing the `_orders` escrow ledger, and the public entrypoint `cancelOrder()` that reaches this function is missing the `nonReentrant` guard present in the mainline contract (`evm/src/apps/IntentGatewayV2.sol:470`). Investigating this exact callback-before-effects surface (the same bug class as the reported SakeVault re-entrance issue) reveals a concrete, provable defect: for same-chain orders with `protocolFeeBps > 0`, `cancelOrder()` passes the **original, pre-fee** `order.inputs` amounts into `withdraw`, while the escrow ledger `_orders[commitment][token]` only ever recorded the **reduced, post-fee** amount. `withdraw` first sends the larger, pre-fee amount out, then attempts `_orders[...] -= amount` with the same larger amount against the smaller recorded balance, causing a guaranteed arithmetic-underflow revert. Because same-chain orders have no other cancellation/withdrawal path, this permanently locks the escrowed input tokens.

### Finding Description
`placeOrder` (evm/tron/contracts/apps/IntentGatewayV2.sol:332-497) computes `reducedInputs[i].amount = originalAmount - protocolFee` and records only the reduced amount in the escrow ledger: [1](#0-0) [2](#0-1) 

`cancelOrder`'s same-chain branch, however, builds the `WithdrawalRequest` directly from `order.inputs` — the **original**, pre-fee amounts — rather than the actually-escrowed (reduced) balances: [3](#0-2) 

`withdraw()` then uses those (too-large) amounts to drive both the external transfer and the ledger decrement, with the transfer happening first:
<cite repo="Annirich/hyperbridge--025" path="evm/tron/contracts/apps/IntentGatewayV2

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L434-436)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L456-458)
```text
                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

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
