## Analog Found: Fee-on-Transfer Token Causes Cross-Chain Supply Inflation in Kaia's Service-Chain Bridge

### Title
Fee-on-transfer ERC20 tokens cause supply inflation between parent/child chains in the Kaia service-chain bridge - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
`BridgeTransferERC20.requestERC20Transfer`/`_requestERC20Transfer` accounts for a cross-chain transfer using the caller-declared `_value` parameter rather than the amount actually received by the bridge contract. When a fee-on-transfer ERC20 token is registered with the bridge and `modeMintBurn` is enabled, an unprivileged user can trigger burning of the full declared `_value` and emission of a `RequestValueTransfer` event carrying that same declared `_value`, even though the bridge contract itself received strictly less than `_value` due to the token's internal transfer fee. The counterpart bridge on the other chain consumes this event and mints the full declared `_value` to the recipient in `handleERC20Transfer`, producing tokens on the destination chain that are not fully backed by tokens actually locked/burned on the source chain.

### Finding Description
`requestERC20Transfer` pulls tokens from the caller with: [1](#0-0) 

It then calls `_requestERC20Transfer`, which — in `modeMintBurn` mode — burns exactly `_value` from the bridge's own balance and emits `RequestValueTransfer` with the same declared `_value`: [2](#0-1) 

Nowhere in this path is the actual balance delta of the bridge contract (before vs. after `safeTransferFrom`) checked against `_value + _feeLimit`. If `_tokenAddress` is a deflationary/fee-on-transfer token (the exact token class used as the PoC in the referenced Allo report — e.g. STA/PAXG-style tokens), the bridge contract will actually receive `(_value + _feeLimit) - transferFee`, i.e. less than the sum requested, yet the code still attempts to burn the full undiminished `_value` and unconditionally emits that same `_value` on-chain in the `RequestValueTransfer` event.

The counterpart bridge on the linked chain processes this event through `handleERC20Transfer`, which trusts the relayed `_value` from the event and mints exactly that amount to the recipient when `modeMintBurn` is enabled: [3](#0-2) 

This is structurally identical to the Allo `poolAmount` bug: a value/amount parameter supplied by the caller is trusted as the "true" transferred quantity and used to update accounting/state (there: `poolAmount` used for later distribution; here: minted supply on the counterpart chain) instead of measuring the actual balance change caused by the token transfer. Any token whose `transferFrom` implementation delivers less than the nominal amount (fee-on-transfer, deflationary, rebasing-down) breaks the 1:1 backing invariant between the two chains.

### Impact Explanation
This breaks the fundamental locked-supply invariant of the mint/burn bridge: tokens minted on the destination chain exceed tokens actually removed from circulation on the source chain. Over repeated transfers of a fee-on-transfer token, this creates unbacked supply inflation on the destination chain — a direct "supply inflation" / "unauthorized value movement" outcome. If the bridge later needs to reconcile balances (e.g., a corresponding withdraw back to the source chain, or in non-mint-burn mode where the bridge holds custody), other users' legitimate transfers can fail or be shortchanged because the bridge's actual token holdings are insufficient to cover the sum of `_value` amounts it has nominally promised across all requests — the same "loss of funds for [other] recipients" pattern described in the source report.

### Likelihood Explanation
Exploitation requires only that: (1) a fee-on-transfer (or otherwise transfer-amount-reducing) ERC20 token is registered with the bridge — a normal governance/operator action that is not inherently suspicious at registration time (many real-world tokens later add or already have such fee behavior, e.g., USDT-style tokens reserve the right to add fees), and (2) `modeMintBurn` is enabled for that token. Once these conditions hold, any unprivileged holder of that token can call the public `requestERC20Transfer` function directly — no special privileges, validator status, or governance access needed to trigger the mismatch.

### Recommendation
In `_requestERC20Transfer` (and `onERC20Received`), measure the bridge contract's actual token balance before and after the `safeTransferFrom` call and use that delta — not the caller-supplied `_value` — to determine (a) how much to burn and (b) what value to emit in `RequestValueTransfer`. Reject or adjust processing when the actual received amount does not match `_value + _feeLimit`.

### Proof of Concept
1. Bridge operators register a fee-on-transfer ERC20 token `T` (deducts `fee` on every `transferFrom`) with `modeMintBurn = true`.
2. Attacker (or any user) holds `T` and calls:
   `requestERC20Transfer(T, recipient, value, feeLimit, extraData)` [1](#0-0) 
3. `safeTransferFrom(msg.sender, address(this), value + feeLimit)` moves only `(value + feeLimit) - fee` tokens into the bridge (per `T`'s fee-on-transfer logic).
4. `_requestERC20Transfer` burns `value` (undiminished) from the bridge's balance and emits `RequestValueTransfer(..., value, ...)`, unaware that the bridge only ever held `value + feeLimit - fee` of `T`. [4](#0-3) 
5. The counterpart chain's bridge operator relays this event to `handleERC20Transfer`, which mints the full `value` of `T` to `recipient` on the destination chain: [5](#0-4) 
6. Net effect: `fee` worth of `T` was never actually removed from circulation on the source chain (it went to `T`'s internal fee sink, e.g., the zero address), yet `value` worth of new `T` was minted on the destination chain — supply inflation equal to the accumulated transfer fees across all such requests.

**Note on limitation:** I was unable to locate the Go/production-side relay logic that decides which `RequestValueTransfer` events get forwarded to `handleERC20Transfer` (it likely lives in `node/sc/bridge_manager.go` or similar, which was only partially covered by search results) to confirm there is no additional balance-reconciliation safeguard at the relay layer. If such a safeguard exists there, it would mitigate (but likely not fully eliminate, since the burn-vs-value mismatch on the source chain is already committed) the impact described above.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L31-73)
```text
    // handleERC20Transfer sends the token by the request.
    function handleERC20Transfer(
        bytes32 _requestTxHash,
        address _from,
        address _to,
        address _tokenAddress,
        uint256 _value,
        uint64 _requestedNonce,
        uint64 _requestedBlockNumber,
        bytes memory _extraData
    )
        public
        onlyOperators
    {
        _lowerHandleNonceCheck(_requestedNonce);

        if (!_voteValueTransfer(_requestedNonce)) {
            return;
        }

        _setHandledRequestTxHash(_requestTxHash);

        handleNoncesToBlockNums[_requestedNonce] = _requestedBlockNumber;
        _updateHandleNonce(_requestedNonce);

        emit HandleValueTransfer(
            _requestTxHash,
            TokenType.ERC20,
            _from,
            _to,
            _tokenAddress,
            _value,
            _requestedNonce,
            lowerHandleNonce,
            _extraData
        );

        if (modeMintBurn) {
            require(ERC20Mintable(_tokenAddress).mint(_to, _value), "handleERC20Transfer: mint failed");
        } else {
            IERC20(_tokenAddress).safeTransfer(_to, _value);
        }
    }
```

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

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L123-135)
```text
    // requestERC20Transfer requests transfer ERC20 to _to on relative chain.
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
