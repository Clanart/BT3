Based on my analysis of the full code, here is my assessment:

---

### Title
Missing `recipient != address(this)` Guard in `ACTION_TRANSFER` Permanently Locks Tokens — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

### Summary

`coreReceiveWithData` with `ACTION_TRANSFER` decodes the recipient entirely from user-supplied `data` and calls `_update(_systemAddress, recipient, amount)` with no check that `recipient != address(this)`. A HyperCore user can craft `data = 0x00 || abi.encode(address(token))`, causing the system address to relay the call and move tokens into the token contract's own balance with no recovery path.

### Finding Description

In `coreReceiveWithData`, the `ACTION_TRANSFER` branch is:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    _update(_systemAddress, recipient, amount);
}
``` [1](#0-0) 

There is no guard of the form `require(recipient != address(this))`. The `data` field is user-controlled: the contract's own NatSpec documents that `coreReceiveWithData` is "invoked by the system address when a HyperCore user triggers `sendToEvmWithData` targeting this token," and the `from` parameter is the originating HyperCore user. [2](#0-1) 

The only caller guard is `msg.sender != _systemAddress`, which is satisfied by the system address faithfully relaying the user's payload. [3](#0-2) 

### Impact Explanation

Once tokens land at `address(this)` via `_update(_systemAddress, address(this), amount)`, there is no sweep or rescue function in `HyperliquedBridgeToken` or its parent `BridgeToken`. [4](#0-3) 

The `ACTION_INIT_TRANSFER` path also moves tokens to `address(this)` but only from `_systemAddress`, and then burns exactly `amount` via `OmniBridge.initTransfer` (which calls `BridgeToken.burn(msg.sender, amount)` where `msg.sender` is the token contract). This does not drain the previously stuck balance — it only burns the freshly moved amount, leaving the stuck tokens permanently unrecoverable. [5](#0-4) [6](#0-5) 

**Impact: Permanent, irrecoverable lock of bridged assets in the token contract** — matching the Critical/permanent-freeze impact category.

### Likelihood Explanation

Any HyperCore user holding a balance of the token can trigger this with a single `sendToEvmWithData` call encoding `address(token)` as the EVM recipient. No privileged access, leaked key, or colluding party is required. The HyperLiquid system address is designed to relay user-provided data verbatim (the `from` field proves the data originates from the user).

### Recommendation

Add a self-transfer guard in the `ACTION_TRANSFER` branch:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    require(recipient != address(this), "HlBridgeToken: self-transfer");
    _update(_systemAddress, recipient, amount);
}
``` [1](#0-0) 

Additionally, consider rejecting `recipient == address(0)` for the same reason.

### Proof of Concept

1. Attacker holds `amount` of `HyperliquedBridgeToken` on HyperCore.
2. Attacker calls HyperCore's `sendToEvmWithData` with:
   - EVM target = `address(token)`
   - `data = bytes.concat(bytes1(0x00), abi.encode(address(token)))`
3. HyperLiquid system address calls `token.coreReceiveWithData(attacker, ..., amount, ..., data)`.
4. Contract decodes `recipient = address(token)`, executes `_update(_systemAddress, address(token), amount)`.
5. `token.balanceOf(address(token)) == amount`.
6. No function in the contract can move or burn this balance — tokens are permanently locked.

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L85-99)
```text
    /// @notice HyperCore -> HyperEVM callback invoked by the system address when a
    /// HyperCore user triggers `sendToEvmWithData` targeting this token.
    /// `destinationRecipient`, `destinationChainId`, and `coreNonce` are CCTP-shaped
    /// and not used here; all routing info comes from `data`.
    /// @dev Accounting model: the 3-arg `mint` parks HyperCore-bound tokens at
    /// `_systemAddress`, so that account holds the standing pool that mirrors total
    /// HyperCore-side balance. HyperLiquid does NOT pre-transfer tokens before this
    /// call fires (the HL system address holds no real ERC20 balance — Circle's
    /// CoreDepositWallet pattern shows the same, with its own pool at `address(this)`).
    /// We pull from `_systemAddress` ourselves; an insufficient pool is a safe revert
    /// that signals an accounting drift between HyperCore and HyperEVM.
    ///
    /// Dispatch:
    /// - data == 0x00 || abi.encode(address recipient): release `amount` from the
    ///   pool to the HyperEVM `recipient`.
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L114-114)
```text
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L120-122)
```text
        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L123-135)
```text
        } else if (action == ACTION_INIT_TRANSFER) {
            (uint128 fee, string memory recipient, string memory message) = abi
                .decode(tail, (uint128, string, string));
            uint128 amount128 = amount.toUint128();
            _update(_systemAddress, address(this), amount);
            IOmniBridgeInitTransfer(owner()).initTransfer(
                address(this),
                amount128,
                fee,
                0,
                recipient,
                message
            );
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L10-81)
```text
contract BridgeToken is
    Initializable,
    UUPSUpgradeable,
    ERC20Upgradeable,
    Ownable2StepUpgradeable,
    IBridgeToken
{
    string internal _name;
    string internal _symbol;
    uint8 internal _decimals;

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        string memory name_,
        string memory symbol_,
        uint8 decimals_
    ) external initializer {
        __ERC20_init(name_, symbol_);
        __UUPSUpgradeable_init();
        __Ownable_init(_msgSender());

        _name = name_;
        _symbol = symbol_;
        _decimals = decimals_;
    }

    function setMetadata(
        string memory name_,
        string memory symbol_,
        uint8 decimals_
    ) external onlyOwner {
        _name = name_;
        _symbol = symbol_;
        _decimals = decimals_;
    }

    function mint(address beneficiary, uint256 amount) external onlyOwner {
        _mint(beneficiary, amount);
    }

    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external virtual onlyOwner {
        _mint(account, value);
    }

    function burn(address account, uint256 value) external onlyOwner {
        _burn(account, value);
    }

    function name() public view virtual override returns (string memory) {
        return _name;
    }

    function symbol() public view virtual override returns (string memory) {
        return _symbol;
    }

    function decimals() public view virtual override returns (uint8) {
        return _decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyOwner {}
}
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L404-405)
```text
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
```
