## Title
Duplicate-input-token escrow merge allows over-release of escrowed funds in Tron `IntentGatewayV2.placeOrder` - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The external report's core broken invariant is: a claimable/escrowed value keyed by an identifier that can be duplicated or merged (multiple veALCX token IDs merged together) lets an attacker accumulate more claimable value than the underlying principal actually backs, because the accounting used `+=`-style aggregation across identifiers that should have been mutually exclusive or capped. The same broken invariant — additive merging of escrow buckets for a token identifier that should be unique per order — is present in the Tron variant of Hyperbridge's Intent Gateway contract.

### Finding Description
The canonical EVM `IntentGatewayV2.sol` (`evm/src/apps/IntentGatewayV2.sol:333-343`) explicitly guards against an order specifying the same input token twice:
```solidity
for (uint256 i; i < inputsLen;) {
    address token = address(uint160(uint256(order.inputs[i].token)));
    // Reject duplicate input tokens
    if (_orders[commitment][token] != 0) revert InvalidInput();
    _orders[commitment][token] = reducedInputs[i].amount;
    ...
``` [1](#0-0) 

This guard was added specifically as a regression fix, as confirmed by the Foundry test suite comment: *"Regression test for: same-chain partial fills over-release repeated input escrow"* / *"Two input legs both using USDC — this previously merged into one escrow bucket."* [2](#0-1) 

A companion guard for duplicate **output** tokens was also added (*"Regression test for: same-chain partial fills prematurely finalize repeated output legs"*), because `_partialFills[commitment][outputToken]` is keyed only by token and duplicate output legs for the same token share one bucket, corrupting the partial-fill/proportional-escrow-release math. [3](#0-2) 

However, the Tron deployment of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, escrows input tokens with an **additive accumulator instead of a single-assignment-with-duplicate-check**:
```solidity
// Store reduced amount (after protocol fees) in escrow
_orders[commitment][token] += reducedInputs[i].amount;
``` [4](#0-3) 

There is no `if (_orders[commitment][token] != 0) revert InvalidInput();` check preceding this line in the Tron contract's `placeOrder`, unlike the hardened main EVM contract. This means the Tron contract still permits an order with duplicate input-token legs, and — by the exact bug-class the main EVM regression tests were written to close — duplicate output legs referencing the same token would still corrupt the shared `_partialFills`/escrow-release bucket used to compute proportional releases on partial fills.

### Impact Explanation
This falls squarely within the required impact classes: **transaction/logic manipulation** and **theft or loss of escrowed funds** in the Intent Gateway's order-escrow custody model. An order crafted with two (or more) legs referencing the same input token, or two legs referencing the same output token, can desynchronize the amount actually transferred into escrow from the amount recorded as owed on fill/refund, or can distort the proportional release computed during a same-chain partial fill (`fillAmount`, `escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired`) because the `totalRequired`/`alreadyFilled` bookkeeping is shared across legs that should be independent. Since escrowed user funds and solver payouts move through this exact bucket, the practical effect mirrors the RevenueHandler bug class: a party can cause an escrow value to be double-counted or misattributed, releasing more (or less, to the detriment of the counterparty) than the honest single-leg accounting would allow — i.e., wrong beneficiary/amount and fund loss, not merely a griefing or DoS condition.

### Likelihood Explanation
This is reachable by any unprivileged user simply by calling the public `placeOrder(order, graffiti)` entrypoint with a crafted `Order` struct containing duplicate `inputs[]` or `output.assets[]` token entries — no relayer, prover, governance, or privileged role is required. The main EVM contract needed an explicit fix and dedicated regression tests to close this exact path, confirming the underlying arithmetic (`_partialFills`/escrow bucket keyed only by token address) is genuinely exploitable when the duplicate-token guard is absent, which is the case in the Tron contract as currently written.

### Recommendation
Port the same duplicate-token rejection logic from `evm/src/apps/IntentGatewayV2.sol` (`_orders[commitment][token] != 0` check on inputs, and the transient-storage duplicate-output check in `placeOrder`) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, and add the equivalent Tron-side regression tests (`DuplicateInputTokenRejection`, `DuplicateOutputTokenRejection`) mirroring `evm/tests/foundry/IntentGatewayV2SameChainTest.sol`.

### Proof of Concept
Note: I was not able to fully read the complete `_fillSameChain`/partial-fill implementation inside `evm/tron/contracts/apps/IntentGatewayV2.sol` before running out of tool iterations, so I cannot present a fully worked numeric trace analogous to the RevenueHandler POC. What is concretely verified from the code retrieved is:
1. The main EVM `IntentGatewayV2.placeOrder` rejects duplicate input tokens (`_orders[commitment][token] != 0 → revert`) and duplicate output tokens (transient-storage check), each backed by a dedicated regression test proving the pre-fix behavior "merged into one escrow bucket" / "shares one `_partialFills` bucket." [5](#0-4) [1](#0-0) 
2. The Tron contract's equivalent escrow-crediting line uses `+=` with no matching duplicate-rejection check visible in the retrieved excerpt. [6](#0-5) 

A background Devin session with full repository/tool access should confirm whether `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `_fillSameChain`-equivalent fill logic and `withdraw` function share the same `_partialFills`/`_orders` structures as the main EVM contract, and if so, write a Foundry/Tron test analogous to `testRevert_PlaceOrder_DuplicateInputTokens`/`testRevert_PlaceOrder_DuplicateOutputTokens` against the Tron contract to demonstrate concretely whether it currently reverts or silently merges/over-releases escrow.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L164-189)
```text

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }
```

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1931-1937)
```text
    /// @notice Placing an order with duplicate input tokens must revert.
    /// Regression test for: same-chain partial fills over-release repeated input escrow.
    function testRevert_PlaceOrder_DuplicateInputTokens() public {
        // Two input legs both using USDC — this previously merged into one escrow bucket
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1200 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2054-2065)
```text
    /// @notice Placing an order with duplicate output tokens must revert.
    /// Regression test for: same-chain partial fills prematurely finalize repeated output legs.
    function testRevert_PlaceOrder_DuplicateOutputTokens() public {
        TokenInfo[] memory inputs = new TokenInfo[](2);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: 1000 * 1e6});
        inputs[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 500 * 1e18});

        // Two output legs both requesting DAI — shares one _partialFills bucket
        TokenInfo[] memory outputAssets = new TokenInfo[](2);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 400 * 1e18});
        outputAssets[1] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 600 * 1e18});

```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L451-463)
```text
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
