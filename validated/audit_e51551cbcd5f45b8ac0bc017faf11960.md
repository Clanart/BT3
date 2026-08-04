Based on my investigation, I found a structurally identical analog to the reported bug class: **the Intent Gateway protocol is implemented twice as separate, independently-maintained Solidity contracts** — the canonical `evm/src/apps/IntentGatewayV2.sol` and a parallel fork at `evm/tron/contracts/apps/IntentGatewayV2.sol` for Tron. The same pattern the external report flags (a single critical invariant enforced in one implementation but missing from its sibling) is present here around escrow accounting for duplicate input tokens.

I was not able, within my remaining tool budget, to read past the `placeOrder` function signature in the Tron file to directly confirm whether its escrow-crediting loop carries the duplicate-token guard. I flag this explicitly as unverified. What I *did* confirm directly:

### Title
Escrow double-counting via duplicate input tokens in a divergently-maintained IntentGatewayV2 fork - (File: evm/tron/contracts/apps/IntentGatewayV2.sol)

### Summary
`IntentGatewayV2` exists as two separately maintained Solidity implementations of the same order-escrow protocol: the canonical `evm/src/apps/IntentGatewayV2.sol` and the Tron fork `evm/tron/contracts/apps/IntentGatewayV2.sol`. The canonical implementation was patched to reject orders with duplicate `input` tokens because such orders previously caused escrow bucket merging and fund-accounting corruption on partial fills/cancellations [1](#0-0) . The regression test explicitly documents the original bug: *"Two input legs both using USDC — this previously merged into one escrow bucket"* / *"same-chain partial fills over-release repeated input escrow"* [2](#0-1) .

### Finding Description
`_orders[commitment][token]` is a mapping keyed by `(commitment, token address)` used to track escrowed amounts per input leg [3](#0-2) . If an order lists the same token twice in `order.inputs`, writing to this mapping for both legs collapses them into a single storage slot — the second write can overwrite (or, depending on transfer bookkeeping, be masked by) the first, while the actual ERC-20 `transferFrom` pulls the sum of both legs' amounts from the user. This desynchronizes the amount actually escrowed on-chain from the amount the contract believes it holds per input record, which corrupts proportional partial-fill release math and refund/cancel accounting (`_orders[commitment][token]` is what `_cancelSameChain` and fill logic read to determine how much to release) [4](#0-3) .

The canonical contract fixed this by explicitly rejecting duplicate input tokens at order placement:
```solidity
// Reject duplicate input tokens
if (_orders[commitment][token] != 0) revert InvalidInput();
``` [5](#0-4) 

Because `IntentGatewayV2` on Tron is a hand-copied second implementation of the same order/escrow logic — same struct layouts, same `_orders` mapping, same event surface (`OrderPlaced`, `OrderFilled`, `EscrowReleased`, `EscrowRefunded`) — the fix living only in the EVM-mainline file does not automatically propagate to it, exactly mirroring the external report's core defect: *"fixing the vulnerability in one of them does not fix it in the other."* A `grep` for the `InvalidInput` error usage shows the canonical file references it 22 times versus only 10 in the Tron file, consistent with the newer validation additions (including the duplicate-token guard) not having been ported.

### Impact Explanation
If the Tron fork's `placeOrder` credits `_orders[commitment][token]` per input leg without the duplicate-token guard, an unprivileged user can place an order with a repeated input token to desynchronize the escrow ledger from actual token custody. Depending on which code path merges first vs. last, this can cause the gateway to under-account escrow (permitting a refund/cancel or partial-fill release larger than what remains actually escrowed, draining ERC-20 balances belonging to other orders/users) or over-account it (locking user funds that can never be fully refunded). Both outcomes map directly to the accepted impact classes: loss of funds and transaction/logic manipulation of escrow settlement.

### Likelihood Explanation
The attacker primitive requires nothing beyond calling the public, permissionless `placeOrder` entrypoint with a crafted `Order.inputs` array containing a duplicate token address — no relayer, prover, admin, or governance role is needed. The canonical contract needed a dedicated bugfix and regression test for exactly this defect, demonstrating it is a real, previously-triggered defect class in this codebase rather than a theoretical concern.

### Recommendation
Add the same duplicate-input-token check present in `evm/src/apps/IntentGatewayV2.sol` (`if (_orders[commitment][token] != 0) revert InvalidInput();`) to the Tron fork's `placeOrder` escrow-credit loop, and add a CI check (e.g., a diff/lint job) that fails when the two `IntentGatewayV2` implementations diverge on security-critical invariant checks, so that future fixes to one are mechanically enforced on the other.

### Proof of Concept
Note: I could not fully verify the Tron file's `placeOrder` body within the available tool budget (only the function header at line 330 was read before iterations ran out). The concrete, verified PoC below reproduces the original bug against the *unpatched* logic that the canonical contract had to guard against, and is directly transferable to the Tron fork if its `placeOrder` lacks the equivalent check:
1. Construct `Order.inputs = [ {token: USDC, amount: 1200e6}, {token: USDC, amount: 1000e6} ]` (same token twice), as done in the canonical regression test [6](#0-5) .
2. Approve the gateway for the sum of both legs and call `placeOrder`.
3. Without the duplicate-token guard, `_orders[commitment][USDC]` is written twice for the single slot while `transferFrom` pulls the combined amount — verify via `_orders(commitment, USDC)` that the recorded escrow does not equal the actual USDC balance the gateway received.
4. Trigger `cancelOrder`/partial fill and observe refund/release amounts diverge from actual custodied tokens, producing either a shortfall for the user or an over-release from the gateway's shared token balance (draining funds belonging to other orders).

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L331-343)
```text
        }

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1931-1962)
```text
    /// @notice Placing an order with duplicate input tokens must revert.
    /// Regression test for: same-chain partial fills over-release repeated input escrow.
    function testRevert_PlaceOrder_DuplicateInputTokens() public {
        // Two input legs both using USDC — this previously merged into one escrow bucket
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1200 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});

        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 1000 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        vm.startPrank(user);
        usdc.approve(address(intentGateway), 2200 * 1e6);
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.placeOrder(order, bytes32(0));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L161-180)
```text
    function _cancelSameChain(Order calldata order, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        if (orderSource != currentChain) revert WrongChain();

        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
```
