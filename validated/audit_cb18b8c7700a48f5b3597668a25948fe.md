### Title
Bridge accepts fabricated 1-step deposit callbacks (`onERC20Received`/`onERC721Received`) from any registered token contract without verifying that the asset was actually transferred, allowing counterpart-chain value inflation - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol, contracts/service_chain/bridge/BridgeTransferERC721.sol)

### Summary
`BridgeTransferERC20.onERC20Received` and `BridgeTransferERC721.onERC721Received` trust `msg.sender` as the deposited token's address and immediately forward it into `_requestERC20Transfer`/`_requestERC721Transfer`, which (in lock/unlock, i.e. non-mint-burn mode) emits `RequestValueTransfer`/`RequestValueTransferEncoded` with **no verification that any balance or NFT was actually moved into the bridge**. This mirrors the reported bug class: any code path that can make a registered token contract itself originate an arbitrary call (analogous to Paraspace's `executeAirdrop`/BendDAO's admin-triggered arbitrary calls) can spoof a deposit and cause the bridge operator to release/mint real value on the counterpart chain for an asset that was never locked.

### Finding Description
`onERC721Received` and `onERC20Received` are public functions that use `msg.sender` as the asset's token-contract address with no additional proof of custody: [1](#0-0) [2](#0-1) 

They call the internal `_request*Transfer` functions, which are gated only by `onlyRegisteredToken`/`onlyUnlockedToken` (an owner-controlled allow‑list), and in lock/unlock mode (`modeMintBurn == false`, the default) perform **no check whatsoever** that the bridge holds the token/NFT before emitting the value-transfer request event that off-chain operators use to mint/release funds on the counterpart chain: [3](#0-2) [4](#0-3) 

The registration list is set by the bridge owner via `registerToken`, which only checks that the address is not already registered — it does not vet the token contract's internal admin logic: [5](#0-4) 

This is structurally the same vulnerability class as the report: the report shows that a lending protocol contract wrongly trusts `msg.sender == NFT contract address` inside `onERC721Received` as proof that a deposit occurred, and that various NFT contracts (BendDAO's `BNFT`, ParaSpace's `NToken` via `executeAirdrop`) allow a privileged (but non-bridge, non-consensus) role to originate arbitrary calldata *as the token contract itself*. If any token registered on the Kaia service-chain bridge has such an "admin can trigger arbitrary call originating as the token contract" feature (a common pattern for airdrop-claim/rescue functions on wrapped/synthetic token contracts), that token's admin — not the bridge owner, not a validator, not a p2p peer — can call `bridge.onERC721Received(...)`/`onERC20Received(...)` directly. Because the bridge's deposit-request logic in non-mint-burn mode does not re-verify that a transfer into the bridge actually happened, the bridge will emit a legitimate-looking `RequestValueTransfer(Encoded)` event, which the counterpart bridge operator (`onlyOperators`) will honor via `handleERC20Transfer`/`handleERC721Transfer`, minting or transferring real value to the attacker-chosen `_to` address on the other chain: [6](#0-5) [7](#0-6) 

In mint-burn mode, `_requestERC20Transfer`/`_requestERC721Transfer` call `burn` on the token contract from the bridge's own context, which requires the bridge to genuinely hold the balance/NFT — so that mode is not exploitable this way. The lock/unlock mode (the default, `modeMintBurn = false`) has no equivalent safeguard.

### Impact Explanation
A successful spoofed callback causes the counterpart-chain bridge operator to mint/release ERC20 tokens or transfer an ERC721 that was never actually locked on the origin chain — a direct, unauthorized cross-chain value creation/duplication. This is a supply-inflation / unauthorized value movement primitive reachable from a single transaction sent by whoever controls the compromised or maliciously-designed admin function of a *registered* token contract (not a bridge/consensus operator). This qualifies as High severity: it can drain the bridge's real backing assets relative to the amount minted on the counterpart side, or double-spend the same underlying asset.

### Likelihood Explanation
Likelihood depends on the existence of a registered token contract with an "arbitrary call as self" admin primitive (e.g., airdrop-claim/rescue functions gated by a role separate from the bridge owner) — this is a real-world pattern seen in various token/NFT projects, as cited in the source report. Given `registerToken` is controlled by the bridge owner and tokens are typically added over time to support ecosystem integrations, the bridge owner cannot fully audit every future admin function of every registered token contract, making this a realistic supply-chain-style risk rather than a purely theoretical one. It requires no p2p/consensus/validator compromise — only a single transaction from an entity with an admin role on a registered token contract.

### Recommendation
- In `onERC20Received`/`onERC721Received`, verify actual custody before crediting a deposit: for ERC721, require `IERC721(msg.sender).ownerOf(_tokenId) == address(this)`; for ERC20, snapshot the bridge's token balance before/after (or otherwise confirm the transfer actually completed) rather than trusting the callback parameters alone.
- Alternatively/additionally, remove the 1-step deposit callback pattern (`onERC721Received`/`onERC20Received`) entirely and require deposits to only occur through the 2-step `transferFrom` + explicit `request*Transfer` path, where the bridge itself pulls the funds and can already confirm receipt.
- Treat `registerToken` as a high-trust operation and require registered tokens to have been vetted for any "admin call as self" functionality before being added to `registeredTokens`.

### Proof of Concept
1. Bridge owner registers token `T` (ERC721) via `BridgeTokens.registerToken(T, cT)`; `modeMintBurn = false` (default lock/unlock mode).
2. Token `T` has an admin-only function (e.g., an airdrop/rescue/claim function comparable to ParaSpace's `executeAirdrop`) that lets its admin execute arbitrary calldata with `msg.sender == address(T)`.
3. `T`'s admin calls this function targeting `bridge`, with calldata `abi.encodeWithSelector(bridge.onERC721Received.selector, attackerFrom, tokenId, victimToAddressOnOtherChain, extraData)`.
4. Inside the bridge, `onERC721Received` executes with `msg.sender == address(T)`, satisfying `onlyRegisteredToken(T)`; `_requestERC721Transfer` performs no ownership check in lock/unlock mode and emits `RequestValueTransferEncoded` for `tokenId` — even though no NFT was ever transferred to the bridge.
5. The counterpart-chain bridge operator observes the event and calls `handleERC721Transfer`, minting/transferring the corresponding NFT to the attacker-controlled address on the counterpart chain — value created without any real deposit. [1](#0-0) [3](#0-2)

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferERC721.sol (L29-71)
```text
    function handleERC721Transfer(
        bytes32 _requestTxHash,
        address _from,
        address _to,
        address _tokenAddress,
        uint256 _tokenId,
        uint64 _requestedNonce,
        uint64 _requestedBlockNumber,
        string memory _tokenURI,
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
            TokenType.ERC721,
            _from,
            _to,
            _tokenAddress,
            _tokenId,
            _requestedNonce,
            lowerHandleNonce,
            _extraData
        );

        if (modeMintBurn) {
            require(ERC721MetadataMintable(_tokenAddress).mintWithTokenURI(_to, _tokenId, _tokenURI), "mint failed");
        } else {
            IERC721(_tokenAddress).transferFrom(address(this), _to, _tokenId);
        }
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC721.sol (L73-106)
```text
    // _requestERC721Transfer requests transfer ERC721 to _to on relative chain.
    function _requestERC721Transfer(
        address _tokenAddress,
        address _from,
        address _to,
        uint256 _tokenId,
        bytes memory _extraData
    )
        internal
        onlyRegisteredToken(_tokenAddress)
        onlyUnlockedToken(_tokenAddress)
    {
        require(isRunning, "stopped bridge");
        (bool success, bytes memory uri) = _tokenAddress.call(abi.encodePacked(ERC721Metadata(_tokenAddress).tokenURI.selector, abi.encode(_tokenId)));
        if (!success) {
            uri = "";
        }
        if (modeMintBurn) {
            ERC721Burnable(_tokenAddress).burn(_tokenId);
        }
        emit RequestValueTransferEncoded(
            TokenType.ERC721,
            _from,
            _to,
            _tokenAddress,
            _tokenId,
            requestNonce,
            0,
            _extraData,
            2,
            abi.encode(string(uri))
        );
        requestNonce++;
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC721.sol (L108-118)
```text
    // onERC721Received function of ERC721 token for 1-step deposits to the Bridge
    function onERC721Received(
        address _from,
        uint256 _tokenId,
        address _to,
        bytes memory _extraData
    )
        public
    {
        _requestERC721Transfer(msg.sender, _from, _to, _tokenId, _extraData);
    }
```

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

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L110-121)
```text
    // onERC20Received function of ERC20 token for 1-step deposits to the Bridge.
    function onERC20Received(
        address _from,
        address _to,
        uint256 _value,
        uint256 _feeLimit,
        bytes memory _extraData
    )
        public
    {
        _requestERC20Transfer(msg.sender, _from, _to, _value, _feeLimit, _extraData);
    }
```

**File:** contracts/service_chain/bridge/BridgeTokens.sol (L56-71)
```text
    // registerToken can update the allowed token with the counterpart token.
    function registerToken(address _token, address _cToken)
        external
        onlyOwner
        onlyNotRegisteredToken(_token)
    {
        // If _cToken == 0 then registeredTokens[_token] = 0, which confuses the
        // onlyRegisteredToken and onlyNotRegisteredToken modifiers.
        require(_cToken != address(0), "counterpart token address is zero");

        registeredTokens[_token] = _cToken;
        indexOfTokens[_token] = registeredTokenList.length;
        registeredTokenList.push(_token);

        emit TokenRegistered(_token);
    }
```
