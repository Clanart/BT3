## Analysis

The external report's core broken invariant: **the code assumes a requested/nominal amount equals the actual amount received after an implicit deduction, then uses the nominal amount for downstream accounting** — creating an accounting entry larger than what was actually taken custody of.

I found a direct, locally-provable analog in the Tron fork of the Intent Gateway.

### Comparison of the two `placeOrder` implementations

The primary EVM contract, `evm/src/apps/IntentGatewayV2.sol`, was hardened specifically against this class of bug: in the non-predispatch branch it records the *actual* balance delta after `safeTransferFrom` before computing protocol fees and crediting escrow: [1](#0-0) 

However, the Tron variant of the same contract, `evm/tron/contracts/apps/IntentGatewayV2.sol`, never adopted this fix. It computes the protocol fee / `reducedInputs` (and therefore the commitment and the escrow credit) straight from the user-supplied `order.inputs[i].amount`, **before** any token transfer happens: [2](#0-1) 

Then, in the direct-escrow branch (no predispatch), it calls `safeTransferFrom` for that same nominal amount and immediately credits `_orders[commitment][token] += reducedInputs[i].amount` — with **no balance-before/after check**: [3](#0-2) 

### Title
Escrow ledger credited with nominal input amount instead of actual tokens received, enabling insolvency/fund drain for non-standard ERC-20s - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder` (Tron variant) computes `reducedInputs` and credits `_orders[commitment][token]` using the caller-supplied `order.inputs[i].amount`, without verifying how many tokens the contract actually received from `safeTransferFrom`. For any ERC-20 that delivers less than the requested amount (fee-on-transfer tokens, tokens with rebasing/blacklist quirks, or tokens whose `transferFrom` silently returns a partial amount), the escrow ledger for that commitment is inflated above the contract's real token balance for that asset.

### Finding Description
`_orders[commitment][token]` is a per-commitment accounting entry, but the underlying tokens live in one shared contract-wide `IERC20` balance across all orders using that token. When `placeOrder` credits `reducedInputs[i].amount` to `_orders[commitment][token]` without confirming that exactly that many tokens landed in the contract, the sum of all `_orders[*][token]` entries can exceed `IERC20(token).balanceOf(address(this))`. When this or another order's escrow is later withdrawn via `withdraw()` (called from `onGetResponse`/settlement flow) at line 690-701, the contract will attempt to pay out the recorded amount from the shared pool. Because the shortfall is fungible, this deficiency doesn't necessarily revert the under-collateralized order itself — it can instead cause a later, unrelated order's withdrawal to fail, or (if withdrawals happen to be processed favorably) let an attacker who deliberately uses a low-fee or fee-on-transfer token drain balance that rightfully belongs to other users' escrowed orders using the same token address. This is the exact PETH-vault pattern: "assume requested amount == received amount, then use the requested amount downstream" — here downstream is the cross-chain settlement/escrow-redemption path rather than Curve liquidity provisioning.

By contrast, the primary `evm/src/apps/IntentGatewayV2.sol` explicitly guards against this with a `balanceOf` delta measurement before computing fees/escrow, confirming this is a known, previously-fixed bug class in this codebase that the Tron fork regressed.

### Impact Explanation
Loss of user/solver funds and false state acceptance in the escrow ledger: the contract's internal accounting (`_orders` mapping) can represent more tokens than it actually custodies for a given ERC-20, which is the exact "stealing or loss of funds" / "logic attack" impact called out by the bounty scope. Any order placed with a token that doesn't deliver the full nominal `transferFrom` amount corrupts the shared token-balance invariant that all other same-token orders rely on for correct withdrawal.

### Likelihood Explanation
High for any deployment where the Tron TRC20 token used as an input implements fee-on-transfer, deflationary, or non-standard transfer semantics (common on Tron's TRC20 ecosystem), or where `safeTransferFrom`'s return value doesn't guarantee the full nominal amount was received. No privileged actor, malicious relayer, or governance action is required — an ordinary unprivileged user placing an order with such a token triggers the corrupted accounting deterministically.

### Recommendation
Mirror the fix already present in `evm/src/apps/IntentGatewayV2.sol`: measure `IERC20(token).balanceOf(address(this))` before and after each `safeTransferFrom` in `placeOrder`'s direct-escrow branch (and the predispatch branch) in `evm/tron/contracts/apps/IntentGatewayV2.sol`, mutate `order.inputs[i].amount` to the actual received delta, and compute `reducedInputs`/the commitment/the escrow credit from that verified amount — never from the caller-supplied nominal amount.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) with an input token `T` that charges e.g. a 2% transfer fee (returns only 98% of the requested amount to the recipient), and `protocolFeeBps = 0` for simplicity.
2. User A places `orderA` with `inputs[0] = {token: T, amount: 1000}`. The contract computes `reducedInputs[0].amount = 1000` (no protocol fee) and calls `safeTransferFrom(A, this, 1000)`, but the contract's `T` balance only increases by `980`. It still executes `_orders[commitmentA][T] += 1000`.
3. User B places `orderB` with the same token `T`, `amount = 1000`, and its transfer also nets the contract `980`. `_orders[commitmentB][T] += 1000`.
4. Contract's actual `T` balance is `1960`, but the sum of ledger entries is `2000` — a 40-token shortfall.
5. When `orderA` is filled and settled (`withdraw()` called via `onAccept`/`onGetResponse`), it pays out `1000` of `T` to the filler, leaving only `960` in the contract.
6. When `orderB` is subsequently filled and settlement attempts to pay out its recorded `1000` of `T`, the contract only holds `960`, causing that withdrawal to revert (locked funds) or, depending on batching/other token inflows, to be paid out of tokens that should have belonged to yet other users — demonstrating the ledger-vs-balance invariant break and consequent fund loss/lock.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L288-292)
```text
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L342-379)
```text
        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
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

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
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
