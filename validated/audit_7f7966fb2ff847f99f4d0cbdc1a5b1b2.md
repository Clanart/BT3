### Title
Unaccounted fee-on-transfer/rebase ERC20 deposits break `BridgeTransferERC20` value-transfer accounting - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
`BridgeTransferERC20.requestERC20Transfer` and `onERC20Received` pull tokens from the user with `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then unconditionally treat `_value` as the exact amount that was moved into (or burned from) the bridge, without ever measuring the bridge's actual token balance before/after the transfer. This mirrors the reported `WERC20` bug class: any ERC20 whose transferred amount does not exactly equal the requested amount (fee-on-transfer tokens, or rebasing/rebase-like tokens with a delayed balance change) will desynchronize the bridge's internal accounting from its real token holdings, causing loss/lock of user funds or incorrect burn/emit accounting.

### Finding Description
`requestERC20Transfer` calls: [1](#0-0) 

It transfers `_value + _feeLimit` via `safeTransferFrom` and then immediately calls `_requestERC20Transfer` using the caller-supplied `_value`, `_feeLimit` figures — never re-checking the actual amount of tokens the bridge received. `_requestERC20Transfer` then either burns `_value` tokens (mint/burn mode) or simply emits an event recording `_value` as the amount to be minted/transferred on the counter-chain: [2](#0-1) 

If the registered ERC20 token deducts a transfer fee (fee-on-transfer token) or is a rebasing token whose `balanceOf` can change after the transfer completes, the bridge's actual balance can differ from `_value` (+`_feeLimit`) that was assumed:
- In non-mint/burn (`lock-and-mint`) mode, the bridge's real token balance can end up lower than what was "promised" in the `RequestValueTransfer` event, so a subsequent legitimate `handleERC20Transfer` on the other chain (or `safeTransfer` released later) could fail or drain other users' locked liquidity, effectively making excess/shortfall funds permanently stuck in - or unbacked by - the bridge contract, exactly as described for `WERC20.burn`/`mint` computing a fixed nominal amount instead of the true balance delta.
- Symmetrically, on receipt (`handleERC20Transfer`), the contract does a plain `safeTransfer(_to, _value)` from bridge-held liquidity without verifying the recipient actually received `_value` (fee-on-transfer would silently short the user), and there is no compensating accounting adjustment anywhere in the contract.

The core root cause is identical to the reported issue: the contract trusts a caller-declared/nominal `uint256 amount` as being equal to the real ERC20 balance delta, instead of measuring `balanceOf(address(this))` before and after the transfer.

### Impact Explanation
Any unprivileged user who calls `requestERC20Transfer`/`onERC20Received` on a bridge configured with a fee-on-transfer or rebasing ERC20 causes the bridge's on-chain event/burn accounting to diverge from its actual token balance. This can permanently lock/lose value for users (their tokens are pulled in but the promised amount cannot be honored on the counter side, or excess accrues in the bridge and is unrecoverable), and in mint/burn mode it can cause `ERC20Burnable.burn(_value)` to attempt to burn an amount the bridge never actually held, reverting user transactions or creating supply/accounting mismatches between the two chains it bridges. This matches the "Medium" fund-loss impact of the reported issue.

### Likelihood Explanation
Likelihood depends on which ERC20 tokens are registered via `registerToken`/`onlyRegisteredToken` for a given bridge deployment; the vulnerability is triggered simply by any user performing a normal `requestERC20Transfer` call using such a token — no privileged action or malicious operator is required to trigger the fund-loss once such a token is registered. This is a standard, well-known ERC20 integration hazard (fee-on-transfer/rebase tokens breaking naive amount-in==amount-received assumptions), directly analogous to the reported `WERC20` bug.

### Recommendation
In `requestERC20Transfer`/`onERC20Received` (and in `handleERC20Transfer`'s outgoing `safeTransfer`), measure `IERC20(_tokenAddress).balanceOf(address(this))` before and after each transfer and use the observed delta as the authoritative `_value`/`fee` used for burning, event emission, and counter-chain minting, rather than trusting the caller-supplied amount. Alternatively, explicitly disallow fee-on-transfer/rebasing tokens from being registered as bridgeable tokens.

### Proof of Concept
1. Deploy `Bridge` in lock-and-mint (`modeMintBurn = false`) mode and register a fee-on-transfer ERC20 token (e.g., 1% fee) via `registerToken`.
2. User calls `requestERC20Transfer(token, to, value=100, feeLimit=0, "")`.
3. `safeTransferFrom` moves `100` nominal tokens from the user, but due to the 1% fee, the bridge only actually receives `99` tokens; `balanceOf(bridge)` increases by `99`, not `100`.
4. `_requestERC20Transfer` still emits `RequestValueTransfer(..., _value=100, ...)`, and the counter-chain operator relays a `handleERC20Transfer` minting/crediting `100` tokens to the recipient, while the bridge is only backed by `99` tokens of real liquidity — a 1-token deficit accrues per transfer, eventually preventing withdrawal of legitimately deposited funds by other users once liquidity is depleted (fund loss/lock), consistent with the referenced `WERC20` `burn`/`mint` bug pattern.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L75-108)
```text
    // _requestERC20Transfer requests transfer ERC20 to _to on relative chain.
    function _requestERC20Transfer(
        address _tokenAddress,
        address _from,
        address _to,
        uint256 _value,
        uint256 _feeLimit,
        bytes memory _extraData
    )
        internal
        onlyRegisteredToken(_tokenAddress)
        onlyUnlockedToken(_tokenAddress)
    {
        require(isRunning, "stopped bridge");
        require(_value > 0, "zero ERC20 token amount");

        uint256 fee = _payERC20FeeAndRefundChange(_from, _tokenAddress, _feeLimit);

        if (modeMintBurn) {
            ERC20Burnable(_tokenAddress).burn(_value);
        }

        emit RequestValueTransfer(
            TokenType.ERC20,
            _from,
            _to,
            _tokenAddress,
            _value,
            requestNonce,
            fee,
            _extraData
        );
        requestNonce++;
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L124-135)
```text
    function requestERC20Transfer(
        address _tokenAddress,
        address _to,
        uint256 _value,
        uint256 _feeLimit,
        bytes memory _extraData
    )
        public
    {
        IERC20(_tokenAddress).safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit));
        _requestERC20Transfer(_tokenAddress, msg.sender, _to, _value, _feeLimit, _extraData);
    }
```
