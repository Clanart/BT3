## Analog Found: Reserved `TRANSACTION_FEES` sentinel shares the same per-token escrow mapping as user-supplied order tokens

### Title
IntentGatewayV2 order tokens can collide with the `TRANSACTION_FEES` sentinel key, corrupting/stealing escrowed fee accounting — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Sherlock Vault bug is a case where a single storage slot serves two different semantic roles — a "global" aggregate account and a "local" per-user account — and a caller can force both roles onto the same key (`address(0)`), after which the wrong write order silently clobbers the correct global data with locally-computed (and therefore wrong) data.

Hyperbridge's `IntentGatewayV2` contracts reproduce the same shared-key pattern: `_orders[commitment][token]` is used both as the **per-input-token escrow ledger** (keyed by real ERC-20/native-token addresses supplied by the user in `order.inputs`/`order.output.assets`) and as the **order-level fee bucket**, keyed by the reserved constant `TRANSACTION_FEES = address(uint160(uint256(keccak256("txFees"))))`. Nothing in `placeOrder` or `withdraw`/`_withdraw` rejects a user-supplied `order.inputs[i].token` (or output token) equal to `TRANSACTION_FEES`. [1](#0-0) [2](#0-1) 

### Finding Description
`placeOrder` escrows each input token under `_orders[commitment][token]` with no check that `token != TRANSACTION_FEES`: [3](#0-2) 

Separately, if `order.fees > 0`, the contract collects the *real* protocol fee token and stamps the fee amount into the very same map slot using a plain **assignment** (`=`, not `+=`): [4](#0-3) 

At settlement, `withdraw()` iterates `body.tokens` (mirroring the user-controlled `order.inputs`) and, for every token in that list, performs a low-level `.call` of `IERC20.transfer` and decrements `_orders[commitment][token]` — then *separately* redeems whatever remains at the `TRANSACTION_FEES` slot as "tx fees", again via a `.call`: [5](#0-4) 

Because `TRANSACTION_FEES` is just a `keccak256`-derived address with no deployed contract at it, a low-level `.call()` to it always returns `success == true` with empty return data (calling a non-contract address executes nothing and "succeeds" per EVM/Solidity semantics). This means:
- An attacker can name `TRANSACTION_FEES` as one of `order.inputs[i].token` in `placeOrder`, crediting `_orders[commitment][TRANSACTION_FEES] += amount` (an accounting entry) while the corresponding `safeTransferFrom`/`.call` for that pseudo-token silently moves nothing.
- As long as the same order also does not set a nonzero `order.fees` (which would overwrite that slot via `=`), the phantom credit under `TRANSACTION_FEES` survives untouched.
- On settlement (`withdraw`), the "redeem tx fees" block reads that phantom balance and pays it out using the contract's **real** protocol fee token (`IDispatcher(host()).feeToken()`), which is a genuine ERC-20 funded by other orders' legitimately escrowed fees — i.e., value the attacker never deposited is paid out from the shared fee-token pool to the attacker-controlled beneficiary.

This is architecturally identical to the Vault bug: a caller-reachable "special" key (`address(0)` in the Vault, `TRANSACTION_FEES` here) is not distinguished from ordinary per-entity keys in the same mapping, and the write that is supposed to represent the aggregate/global value can be forged or overwritten by manipulating the local/per-entry write path that shares the same slot.

The identical unguarded pattern (`mapping(bytes32 => mapping(address => uint256)) public _orders`, plus `_orders[commitment][TRANSACTION_FEES]` used for pooled fees) also exists in the newer intents contracts: [6](#0-5) [7](#0-6) [8](#0-7) 

### Impact Explanation
If exploitable, this lets an attacker fabricate escrow-accounting entries that are later redeemed against the contract's real, shared protocol-fee-token balance — a direct "stealing/loss of funds" and "logic attack via double-claim/incorrect-beneficiary" impact matching the Hyperbridge bounty gate. It also can silently corrupt legitimate orders' fee accounting (an attacker's phantom credit combined with a later real-fee assignment causes the real fee escrow to be overwritten/lost, or vice versa), producing wrong global fee-pool bookkeeping — precisely the "wrong data overwrites correct data" pattern flagged in the source report.

### Likelihood Explanation
The `TRANSACTION_FEES` value is a fixed, publicly computable constant with no code deployed at it, reachable from the fully public, unprivileged `placeOrder` entrypoint — no relayer, prover, or governance action is required. The only open question (noted below) is whether the exact `SafeERC20`/`Address` library version imported by this contract reverts a `safeTransferFrom` call whose target has no code, which determines whether the "phantom deposit" leg of the exploit succeeds as described.

### Recommendation
Explicitly reject `TRANSACTION_FEES` (and any other reserved sentinel) as a valid `order.inputs[]`/`order.output.assets[]` token in `placeOrder`'s validation loop, and/or move the fee ledger into its own dedicated mapping (e.g. `mapping(bytes32 => uint256) _orderFees`) that is namespace-isolated from the token-address-keyed `_orders` escrow map, so no user-suppliable token value can ever collide with the fee-accounting key — mirroring the Vault fix's approach of never letting a caller force two distinct accounting roles onto one key.

### Proof of Concept
1. Attacker calls `placeOrder` with `order.inputs = [{token: TRANSACTION_FEES, amount: X}]`, `order.fees = 0`.
2. `IERC20(TRANSACTION_FEES).safeTransferFrom(attacker, address(this), X)` targets a non-contract address; depending on the exact `SafeERC20`/`Address.functionCall` implementation in use, this either (a) silently "succeeds" without moving value (pre-contract-check OZ versions / raw `.call` semantics), or (b) reverts with `"Address: call to non-contract"` (OZ versions that assert code size). **This distinction could not be fully verified from the indexed code alone and should be confirmed by inspecting the exact OpenZeppelin version vendored/imported by this contract.**
3. Assuming (a): `_orders[commitment][TRANSACTION_FEES] += X` records a phantom escrow credit with no backing funds.
4. On fill/cancel, `withdraw()`'s "redeem tx fees" block reads `_orders[commitment][TRANSACTION_FEES] = X` and calls the real fee-token contract's `transfer(beneficiary, X)`, paying out `X` of the shared protocol fee token to the attacker from funds actually deposited by other users' orders.

Because verification of step 2's exact revert behavior requires reading the vendored `SafeERC20`/`Address` library source (not retrieved in this session), this finding should be validated against the specific library version before being treated as fully confirmed; the underlying design defect — an unguarded reserved sentinel sharing a mapping namespace with attacker-controlled token addresses — is confirmed directly from the cited code regardless of that library detail.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L79-83)
```text
    /**
     * @dev Address constant for transaction fees, derived from the keccak256 hash of the string "txFees".
     * This address is used to store or reference the transaction fees within the contract.
     */
    address private constant TRANSACTION_FEES = address(uint160(uint256(keccak256("txFees"))));
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L117-123)
```text
    /**
     * @dev Mapping to store orders.
     * The outer mapping key is a bytes32 value representing the order commitment.
     * The inner mapping key is an address representing the escrowed token contract.
     * The inner mapping value is a uint256 representing the order amount.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L465-482)
```text
        if (order.fees > 0) {
            // escrow fees
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-714)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L333-362)
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

        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }

            _orders[commitment][TRANSACTION_FEES] = order.fees;
        }
```
