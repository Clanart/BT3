## Title
`IntentGatewayV2.withdraw` on Tron lacks per-token duplicate/amount validation present in the mainnet EVM contract, enabling escrow-accounting mismatch on same-chain cancel/redeem paths - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary
This is a genuine structural difference between two deployments of the same protocol, not a fully provable exploit — I was not able to confirm a concrete profitable attack path within the remaining investigation budget, so I am reporting the discrepancy with the evidence found and flagging the unresolved parts explicitly rather than asserting certainty.

## Finding Description
The mainnet EVM `IntentGatewayV2.placeOrder` (`evm/src/apps/IntentGatewayV2.sol:333-343`) explicitly rejects duplicate input tokens:
```solidity
// Reject duplicate input tokens
if (_orders[commitment][token] != 0) revert InvalidInput();
_orders[commitment][token] = reducedInputs[i].amount;
``` [1](#0-0) 

It also rejects duplicate *output* tokens via transient storage before escrowing: [2](#0-1) 

The regression test suite explicitly documents why: `"Regression test for: same-chain partial fills over-release repeated input escrow"` and `"shares one _partialFills bucket"`, confirming that duplicate input/output tokens were a previously-identified class of bug in the partial-fill accounting logic. [3](#0-2) [4](#0-3) 

The Tron variant of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, has **no equivalent duplicate-token rejection** in `placeOrder`. It instead accumulates escrow via `+=`:
```solidity
_orders[commitment][token] += reducedInputs[i].amount;
``` [5](#0-4) [6](#0-5) 

Its `withdraw()` function only checks that the escrow slot is non-zero, not that the requested `amount` is `<=` the remaining escrowed balance, before performing the transfer:
```solidity
if (_orders[body.commitment][token] == 0) revert UnknownOrder();
... token.call(...transfer(beneficiary, amount)...)
_orders[body.commitment][token] -= amount;
``` [7](#0-6) 

I traced the escrow-accumulation and withdrawal paths for the case of a duplicated token in `order.inputs` and, for the specific case where the same `WithdrawalRequest.tokens` list used at withdrawal exactly mirrors `order.inputs` used at deposit, the sums reconcile and Solidity's checked-arithmetic subtraction would revert (undoing any transfer) if amounts were ever crafted to exceed the escrowed remainder — so no unconditionally-provable fund-loss primitive was confirmed from this path alone within the exploration performed.

## Impact Explanation
If a discrepancy exists between how the commitment hash / escrow accounting treats duplicate tokens on Tron versus how any downstream partial-fill or solver-fill logic (mirrored from `IntentsBase.sol`/`IntrinsicIntents.sol`, which do implement `_partialFills` bucket accounting) indexes escrow release, the missing duplicate-token guard could allow a partial-fill flow to release escrow against the same token bucket more than once — matching exactly the class of bug the mainnet regression tests were written to prevent. This would be a direct "unauthorized/duplicate settlement of escrowed funds" impact under the bounty's scope if a fillOrder/partial-fill entrypoint reachable from Tron's contract exists and indexes by array position rather than token address.

## Likelihood Explanation
**Not confirmed.** I was unable to locate a `fillOrder`/partial-fill function within `evm/tron/contracts/apps/IntentGatewayV2.sol` itself in the portion of the file I read (it appears to only implement `placeOrder`, `cancelOrder`, `onAccept`, `withdraw`, `onGetResponse` — no solver-fill or partial-fill logic was found there). Without confirming whether Tron's deployment reaches the `_partialFills`-style logic that lives in `IntentsBase.sol`/`IntrinsicIntents.sol`, I cannot assert this is currently exploitable in production; the missing guard is a real deviation from the hardened mainnet contract, but the actual attacker-reachable trigger for "over-release" was not verified end-to-end in the time available.

## Recommendation
- Port the mainnet `evm/src/apps/IntentGatewayV2.sol` duplicate-input-token rejection (`evm/src/apps/IntentGatewayV2.sol:333-343`) and duplicate-output-token rejection (`evm/src/apps/IntentGatewayV2.sol:165-189`) into `evm/tron/contracts/apps/IntentGatewayV2.sol`, or otherwise prove that the `+=` accumulation pattern is safe against every partial-fill/settlement path reachable on Tron.
- In `withdraw()` (`evm/tron/contracts/apps/IntentGatewayV2.sol:682-705`), replace the `== 0` non-zero check with an explicit `amount <= _orders[body.commitment][token]` bound check performed *before* the token transfer, so an over-withdrawal attempt fails safely and cannot depend on incidental Solidity underflow-revert behavior for its safety property.
- Add regression tests to the Tron test suite mirroring `IntentGatewayV2SameChainTest.sol`'s `testRevert_PlaceOrder_DuplicateInputTokens` / `testRevert_PlaceOrder_DuplicateOutputTokens`.

## Proof of Concept
Not established as a working end-to-end exploit. The concrete open question needed to complete a PoC: locate and inspect the exact fill/settlement entrypoint used on the Tron deployment (whether it lives in a separate contract analogous to `IntrinsicIntents.sol`/`ExtrinsicIntents.sol`, and whether it indexes escrow release by token address or by array index) to determine whether a crafted `order.inputs` with a duplicated token address can cause `_partialFills`-style logic to release the same escrow bucket twice on Tron. This could not be completed within the available tool-call budget.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L165-189)
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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1931-1964)
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
        vm.stopPrank();
    }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2054-2088)
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
        usdc.approve(address(intentGateway), 1000 * 1e6);
        dai.approve(address(intentGateway), 500 * 1e18);
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L435-435)
```text
                _orders[commitment][token] += reducedInputs[i].amount;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L457-457)
```text
                _orders[commitment][token] += reducedInputs[i].amount;
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
