## Finding

### Title
Wrapped-token bridge trusts nominal `amount` instead of tokens actually received, letting fee-on-transfer/deflationary ERC20s mint more value on the destination chain than was locked on the source - ([File: sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol])

### Summary
`WrappedHyperFungibleToken.send()` locks the underlying ERC20 via `safeTransferFrom` and then encodes the cross-chain message using the caller-supplied `params.amount` rather than the amount actually received by the contract. For any underlying token that deducts a fee/burn on transfer (fee-on-transfer, deflationary, or an upgradeable proxy token that adopts such semantics after an upgrade — exactly the Polkaswap/SORA report's scenario), the contract escrows less than `params.amount` but still instructs the destination chain to unlock/mint the full nominal `params.amount` to the beneficiary.

### Finding Description
In `send()`: [1](#0-0) 
the contract does `IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount)` without measuring the actual token delta received.

`_buildDispatchPost` then encodes the message body using the untouched `params.amount`: [2](#0-1) 

This `amount` is exactly what the destination-chain peer contract trusts and pays out in `onAccept`: [3](#0-2) 

The codebase's own `IntentGatewayV2.sol` explicitly recognizes and guards against this exact class of bug by measuring `balanceOf` before/after the transfer and using the *actual received* amount for escrow/commitment purposes: [4](#0-3) [5](#0-4) 

`WrappedHyperFungibleToken.sol` (and its upgradeable sibling `WrappedHyperFungibleTokenUpgradeable.sol`, which shares identical logic) has no such balance-diff check, so it blindly trusts that the ERC20's `transferFrom` moves exactly `params.amount` into the contract.

### Impact Explanation
Because the destination-side `onAccept` unconditionally transfers `message.amount` of the underlying to the beneficiary (or mints/unlocks the corresponding representation on that peer), an attacker using a fee-on-transfer or newly-upgraded-to-deflationary underlying token can lock less value than the amount that gets credited across the bridge. Repeated over multiple sends, this drains the shared underlying-token reserve held by the peer `WrappedHyperFungibleToken` contract on the other chain — funds legitimately deposited by other users are paid out to the attacker, i.e. direct loss of bridged funds / wrong amount released, matching the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" categories.

### Likelihood Explanation
This requires only an unprivileged, permissionless call to `send()` with an underlying token that has fee-on-transfer or deflationary semantics (or a proxy token that adopts such semantics via an ordinary upgrade, as in the referenced report) — no relayer, prover, or admin collusion needed. The likelihood is governed entirely by which underlying token the deployment's owner configures via `configure()`; if any configured underlying is upgradeable or already fee-on-transfer, the path is directly exploitable by any user.

### Recommendation
Mirror the `IntentGatewayV2.sol` pattern: snapshot `IERC20(_underlying).balanceOf(address(this))` before and after `safeTransferFrom` in `send()`, and use the actual received delta (not `params.amount`) both for the dispatched message body and the `Sent` event. Apply the same fix to `WrappedHyperFungibleTokenUpgradeable.sol`. Additionally, consider disabling or flagging support for tokens that are provably fee-on-transfer or freely upgradeable until this accounting fix lands.

### Proof of Concept
1. Owner configures `WrappedHyperFungibleToken` with an underlying ERC20 `T` that charges a 2% fee on every transfer (or an upgradeable token that is later upgraded to add such a fee).
2. Attacker calls `send({amount: 100e18, to: attackerOnDest, dest: chainB, ...})` after approving `100e18` of `T`.
3. `safeTransferFrom` moves only `98e18` into the contract (2e18 burned/fee), but `_buildDispatchPost` encodes `amount = 100e18` in the ISMP message.
4. On chain B, `onAccept` executes `IERC20(_underlying).safeTransfer(beneficiary, 100e18)`, paying out `100e18` even though only `98e18` worth of value was ever escrowed on chain A.
5. Repeating this drains the peer contract's underlying reserve, causing loss of funds for other users whose locked tokens back that reserve.

### Citations

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L234-253)
```text
    function _buildDispatchPost(HyperFungibleToken.SendParams calldata params) internal view returns (DispatchPost memory) {
        bytes memory dest = _supportedChains[params.dest];
        if (dest.length == 0) revert UnsupportedChain();

        bytes memory body = abi.encode(HyperFungibleToken.Message({
            from: abi.encodePacked(msg.sender),
            to: params.to,
            amount: params.amount,
            data: params.data
        }));

        return DispatchPost({
            dest: params.dest,
            to: dest,
            body: body,
            timeout: params.timeout,
            fee: params.relayerFee,
            payer: msg.sender
        });
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-273)
```text
    function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
        uint256 msgValue = msg.value;
        if (_isWeth && msgValue >= params.amount) {
            msgValue = msgValue - params.amount;
            IWETH(_underlying).deposit{value: params.amount}();
        } else {
            IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
        }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-324)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L198-201)
```text
        // Phase 1: Transfer tokens and record actual received amounts.
        // For fee-on-transfer tokens, the gateway receives less than the requested amount.
        // We mutate order.inputs to reflect actual received so the commitment and escrow
        // are consistent with what the gateway holds.
```

**File:** evm/src/apps/IntentGatewayV2.sol (L286-292)
```text
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }
```
