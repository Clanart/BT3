Based on the evidence I was able to gather, the strongest local analog to the "duplicate token" bug class is a **cross-implementation regression in the Tron deployment of `IntentGatewayV2`**, which is a hand-maintained, non-inheriting duplicate of the audited/fixed EVM contract.

### Title
Tron `IntentGatewayV2` reimplements order escrow without inheriting the duplicate-input/output-token guard added to the canonical EVM contract - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The canonical EVM `IntentGatewayV2` (via `evm/src/apps/intentsv2/IntentsBase.sol`) was hardened against the exact bug class described in the external report — orders with duplicate input or output tokens, which caused a single escrow/settlement bucket to be shared by two distinct legs, enabling over-release of escrow or premature/duplicate finalization. This is confirmed by regression tests `testRevert_PlaceOrder_DuplicateInputTokens` and `testRevert_PlaceOrder_DuplicateOutputTokens` in [1](#0-0) , which explicitly revert with `IntentsBase.InvalidInput.selector` and are annotated "Regression test for: same-chain partial fills over-release repeated input escrow" / "prematurely finalize repeated output legs" [2](#0-1) .

However, `evm/tron/contracts/apps/IntentGatewayV2.sol` is **not** a thin wrapper around the shared `IntentsBase`/`IntentGatewayV2` logic — it is a standalone contract (`contract IntentGatewayV2 is HyperApp, EIP712`) that only imports shared *types* from `@hyperbridge/core/apps/IntentGatewayV2.sol`, not the validation base contract [3](#0-2) . A repo-wide search for the string "Duplicate" found matches only in `evm/src/core/HandlerV2.sol` and the EVM test suite — none in the Tron contract file — while the Tron file otherwise mirrors the same escrow/withdrawal design (`_orders[commitment][token]` accounting, `withdraw()` looping over `body.tokens` by index) as seen in [4](#0-3) .

### Finding Description
The root cause pattern from the external report is: an array of `(token, amount)` entries is accepted without rejecting duplicate token addresses, and downstream settlement logic keys off "first occurrence" or per-token indexed accounting that assumes uniqueness. Hyperbridge's `IntentGatewayV2` order/escrow model uses exactly this structure — `Order.inputs: TokenInfo[]` and `PaymentInfo.assets: TokenInfo[]`, escrowed and released per-token via `_orders[commitment][token]` [5](#0-4) .

The canonical EVM contract's fix rejects orders where two input legs or two output legs reference the same token, because merging them into one escrow bucket breaks partial-fill/withdrawal accounting (confirmed by the two regression tests). The Tron contract, being an independently maintained copy that does not inherit `IntentsBase`, does not surface any equivalent "Duplicate" check in its source, and its `placeOrder`/escrow bookkeeping (`_orders[commitment][token]`, `withdraw()`) is structurally identical to the vulnerable pre-fix pattern — a loop that operates on `body.tokens[i]` by index with per-token balance subtraction, which is exactly the shape that duplicate-token entries corrupt.

### Impact Explanation
If Tron's `placeOrder` still permits duplicate input tokens, a user's order with two USDC input legs would escrow into a single `_orders[commitment][USDC]` bucket sized incorrectly relative to what the solver/protocol expects to release on fill, mirroring the "over-release repeated input escrow" and "prematurely finalize repeated output legs" scenarios explicitly called out as the regression being guarded against on EVM. This falls squarely under the bounty's "duplicate settlement" and "wrong beneficiary or amount" categories for bridged intent escrow.

### Likelihood Explanation
Placing an order is a fully public, unprivileged entrypoint requiring no relayer, prover, or admin involvement — the same access level as the original report's `enableSession()`/`createInvoice()` calls. The likelihood hinges entirely on whether Tron's `placeOrder` implementation independently re-derived the duplicate check; I was not able to read the full body of Tron's `placeOrder`/`fillOrder` functions before running out of tool budget, so I cannot confirm with certainty that the check is absent — only that (a) the fix lives in a separate inherited base (`IntentsBase.sol`) that Tron does not use, and (b) no "Duplicate" identifier appears anywhere in the Tron file.

### Recommendation
Verify the full `placeOrder` and `fillOrder` bodies in `evm/tron/contracts/apps/IntentGatewayV2.sol` for an explicit duplicate-token rejection equivalent to the EVM `IntentsBase` check. If absent, port the same validation (reject `Order.inputs`/`PaymentInfo.assets` containing repeated `token` values) into the Tron contract before any Tron-chain deployment, and add the equivalent of `testRevert_PlaceOrder_DuplicateInputTokens` / `testRevert_PlaceOrder_DuplicateOutputTokens` to the Tron test suite.

### Proof of Concept
Conceptual, based on the confirmed EVM regression test pattern [6](#0-5) : construct an `Order` with `inputs = [ {token: USDC, amount: 1200e6}, {token: USDC, amount: 1000e6} ]` and call Tron's `placeOrder`. On the EVM contract this reverts with `InvalidInput`; if Tron's contract accepts it (unverified due to incomplete read of the file), the two legs collapse into a single `_orders[commitment][USDC]` escrow entry, and subsequent `withdraw()`/fill accounting — which iterates `body.tokens[i]` and does `_orders[commitment][token] -= amount` — will double-count or under-release against the actual escrowed balance, per the same mechanism as the original report's second-token-never-reached failure.

**Caveat:** I could not fully verify the Tron contract's `placeOrder`/`fillOrder` implementation bodies within the available tool budget, so this should be treated as a strong structural lead requiring direct code confirmation rather than a fully proven vulnerability.

### Citations

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L21-55)
```text
import {
    PaymentInfo,
    TokenInfo,
    DispatchInfo,
    Order,
    SweepDust,
    Params,
    ParamsUpdate,
    DestinationFee,
    WithdrawalRequest,
    FillOptions,
    SelectOptions,
    CancelOptions,
    NewDeployment
} from "@hyperbridge/core/apps/IntentGatewayV2.sol";
import {IIntentPriceOracle} from "@hyperbridge/core/apps/IntentPriceOracle.sol";

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";

import {IUniswapV2Router02} from "@uniswap/v2-periphery/contracts/interfaces/IUniswapV2Router02.sol";
import {ICallDispatcher, Call} from "../../../src/interfaces/ICallDispatcher.sol";


/**
 * @title IntentGatewayV2
 * @author Polytope Labs (hello@polytope.technology)
 *
 * Implements the IntentGatewayV2 contract for Tron
 *
 * @dev The IntentGateway allows for the creation and fulfillment of same-chain & cross-chain orders.
 */
contract IntentGatewayV2 is HyperApp, EIP712 {
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
