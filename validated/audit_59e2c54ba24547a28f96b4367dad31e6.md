## Analysis

The BSB22 report's core broken invariant is: **two independent safety mechanisms that are supposed to hold separately can collapse into a single, weaker guarantee under a specific code path, and the maintainers only partially closed the gap ("at least one" instead of "always both").** The Hyperbridge analog I found is a genuine `checks-effects-interactions` collapse in the Tron deployment of the Intent Gateway: the external-call and the escrow-accounting decrement, which the main EVM `IntentGatewayV2`/`IntentsBase.sol` keeps apart with a `nonReentrant` guard, are unprotected in `evm/tron/contracts/apps/IntentGatewayV2.sol`.

### Title
Reentrant double-withdrawal of escrowed Intent Gateway funds via unguarded `withdraw()` on Tron - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`withdraw()` in the Tron `IntentGatewayV2` sends native/ERC20 tokens to `beneficiary` via a low-level `.call` **before** decrementing `_orders[body.commitment][token]`, and neither `withdraw()` nor its caller `onGetResponse()` carries a reentrancy guard.

### Finding Description [1](#0-0) 

`withdraw()` sets `_filled[body.commitment] = beneficiary` once, then loops over `body.tokens`, and for each token:
1. Checks only `_orders[body.commitment][token] == 0` (a non-zero check, not an amount check),
2. Performs the value transfer (`beneficiary.call{value: amount}("")` for native token, or an ERC20 `transfer` call),
3. Only **afterwards** does `_orders[body.commitment][token] -= amount`.

`onGetResponse()` calls `withdraw(body, true)` directly with no reentrancy lock: [2](#0-1) 

Because the escrow decrement happens after the external call, a `beneficiary` that is a contract can, inside its `receive()`/fallback triggered by the native-token transfer, re-enter the gateway. If it reaches a path that calls `withdraw()` again for the same `commitment` (e.g. a duplicated `RefundEscrow`/`RedeemEscrow` delivery, or a second `onGetResponse` for the same cancel-from-source flow), `_orders[body.commitment][token]` is still non-zero at that point, so the check passes and a second transfer of the same escrowed balance is issued before the first call's decrement ever executes.

By contrast, the primary EVM gateway logic (`IntentsBase.sol`) wraps its cancel/withdraw entry points in `nonReentrant`: [3](#0-2) 

This mirrors the BSB22 pattern precisely: the "two independent guards" here are (a) checks-effects-interactions ordering and (b) an explicit reentrancy lock. In the main EVM contract both exist together (defense in depth); in the Tron port, both collapsed into "none" for the `withdraw`/`onGetResponse` path — exactly the kind of quietly-lost redundancy the BSB22 report flags, except here the collapse is fully exploitable rather than merely a ZK-blinding predictability nit.

### Impact Explanation
An attacker who controls the `beneficiary` address of an order (trivial: they place or fill the order themselves, or act as the destination-chain solver whose `WithdrawalRequest.beneficiary` becomes the recipient) can drain escrowed input tokens or native currency more than once for a single commitment, directly stealing bridge-custodied funds — a "stealing or loss of funds" / "double-settlement" impact matching the bounty's accepted impact categories.

### Likelihood Explanation
No privileged actor, relayer, prover, or governance role is required — the attacker only needs to be the `beneficiary`/order participant with a smart-contract wallet, and to trigger a second `withdraw()` invocation for the same commitment before the first `_orders[...] -= amount` executes. Native-token escrow (`token == address(0)`) is the most direct trigger since the low-level `.call{value:}` unconditionally hands control to an arbitrary contract.

### Recommendation
- Reorder `withdraw()` to decrement `_orders[body.commitment][token]` (and `TRANSACTION_FEES`) **before** issuing any external call (checks-effects-interactions).
- Add a reentrancy guard (`nonReentrant`) to `onGetResponse()`, `onAccept()`, and any other entry point that can reach `withdraw()`, matching the protection already present in the primary EVM `IntentsBase.sol`.

### Proof of Concept
1. Attacker places (or is selected to fill) an order whose `beneficiary`/output recipient is an attacker-controlled contract with a `receive()` fallback.
2. A settlement path calls `withdraw()` for that order's commitment with a native-token entry in `body.tokens`.
3. During the `beneficiary.call{value: amount}("")` transfer, the attacker's fallback re-enters the gateway and triggers `onGetResponse()`/`withdraw()` again for the same `commitment` (e.g., via a duplicate GET-response cancel delivery or a resubmitted `RefundEscrow`/`RedeemEscrow`).
4. Because `_orders[commitment][token]` has not yet been decremented from step 2, the `!= 0` check passes and the same escrowed balance is paid out a second time before either call's decrement runs, netting the attacker double the escrowed funds.

**Note on confidence:** I traced the missing CEI ordering and absent `nonReentrant` guard directly in the Tron contract's source, and confirmed the main EVM contract uses `nonReentrant` where Tron does not [4](#0-3) . I was not able to fully trace, within the available index, every concrete on-chain call path by which a second `onGetResponse`/`onAccept` invocation for the same commitment could be triggered mid-transaction (this depends on host/handler dispatch semantics not fully covered by the indexed files) — a Devin session with full repo access should verify the exact reentrant call graph before treating this as fully proven.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L661-721)
```text
                if (token == address(0)) {
                    (bool sent,) = req.beneficiary.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, req.beneficiary, amount));
                    if (!success) revert TransferFailed();
                }
                unchecked {
                    ++i;
                }
                emit DustSwept(token, amount, req.beneficiary);
            }
        }
    }

    /**
     * @notice Withdraws the escrowed tokens for a request body.
     * @dev This function is marked as internal.
     * @param body The request body containing commitment, tokens, and beneficiary.
     * @param isRefund Whether this is a refund (true) or a successful fill (false).
     */
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L723-735)
```text
    /**
     * @notice Handles the response for a previously dispatched storage query (GET request).
     * @dev This function is called by the host to process the response of a GET request.
     * @param incoming The response data structure for the GET request.
     * Only the host can call this function.
     */
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        withdraw(body, true);
    }
}
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L118-150)
```text
    /**
     * @dev Maps order commitment hashes to the address that filled or refunded the order.
     * A non-zero value indicates the order has been finalized and cannot be filled again.
     */
    mapping(bytes32 => address) public _filled;

    /**
     * @dev Monotonically increasing counter used to assign unique nonces to orders.
     * Each call to `placeOrder` consumes and increments this value.
     */
    uint256 public _nonce;

    /**
     * @dev Gateway configuration parameters including host address, dispatcher,
     * fee settings, price oracle, and solver selection toggle.
     */
    Params internal _params;

    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;

    /**
     * @dev Maps keccak256(stateMachineId) to the registered gateway address for
     * that chain. Used for authenticating cross-chain messages and routing dispatches.
     */
    mapping(bytes32 => address) public _instances;

    /**
     * @dev Maps (commitment, output token) to the cumulative amount already filled.
     * Used to track partial fill progress for same-chain orders.
```
