### Title
`HyperliquedBridgeToken._systemAddress` Is Never Initialized When Deployed via `OmniBridge.deployToken()`, Permanently Bricking `coreReceiveWithData()` — (File: `evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`OmniBridge.deployToken()` always initializes new token proxies using the 3-argument `BridgeToken.initialize` selector. When `HyperliquedBridgeToken` is used as the `tokenImplementationAddress`, its critical `_systemAddress` field is never set (remains `address(0)`). Because `coreReceiveWithData()` gates all execution on `msg.sender == _systemAddress`, and `msg.sender` can never equal `address(0)`, the function permanently reverts for every deployed token. There is no post-deployment setter for `_systemAddress`, so the condition is irrecoverable.

---

### Finding Description

`OmniBridge.deployToken()` creates every bridge token proxy with a hardcoded 3-argument initializer call:

```solidity
new ERC1967Proxy(
    tokenImplementationAddress,
    abi.encodeWithSelector(
        BridgeToken.initialize.selector,   // 3-arg: name, symbol, decimals
        metadata.name,
        metadata.symbol,
        decimals
    )
)
``` [1](#0-0) 

`HyperliquedBridgeToken` provides its own 5-argument `initialize()` that sets `_systemAddress` and `hyperCoreDeployer`:

```solidity
function initialize(
    string memory name_,
    string memory symbol_,
    uint8 decimals_,
    address systemAddress_,
    address hyperCoreDeployer_
) external initializer {
    ...
    _systemAddress = systemAddress_;
    ...
}
``` [2](#0-1) 

Because `OmniBridge.deployToken()` calls `BridgeToken.initialize.selector` (3-arg), the parent's `initialize()` runs instead, leaving `_systemAddress` at its zero-value default (`address(0)`). The `initializer` modifier then marks the contract as initialized, so the 5-arg `HyperliquedBridgeToken.initialize()` can never be called afterward.

`coreReceiveWithData()` enforces:

```solidity
if (msg.sender != _systemAddress) revert NotSystemAddress();
``` [3](#0-2) 

With `_systemAddress == address(0)`, this check becomes `msg.sender != address(0)`, which is always `true`. Every call to `coreReceiveWithData()` reverts unconditionally. There is no `setSystemAddress()` or equivalent function anywhere in `HlBridgeToken.sol` or `BridgeToken.sol` to recover from this state. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

`coreReceiveWithData()` is the sole on-chain entry point for HyperCore → HyperEVM token bridging. When a HyperCore user triggers `sendToEvmWithData`, the HyperLiquid system address calls `coreReceiveWithData()` on the token contract. If that call always reverts, the user's HyperCore-side balance is debited but no corresponding EVM-side credit or bridge transfer is ever executed. The tokens are effectively locked with no recovery path, because:

1. `_systemAddress` cannot be updated post-initialization.
2. The `initializer` guard prevents re-running the 5-arg `initialize()`.
3. Neither `OmniBridge` nor `BridgeToken` exposes any admin function to set `_systemAddress` on a deployed proxy. [5](#0-4) 

---

### Likelihood Explanation

**High.** The `HyperliquedBridgeToken` is purpose-built to be used as the `tokenImplementationAddress` in `OmniBridge` for Hyperliquid deployments. Any operator who sets `HyperliquedBridgeToken` as the implementation and then calls `deployToken()` (a permissionless, publicly callable function after the admin sets the implementation) will produce broken token proxies. The flaw is structural and silent — the proxy deploys successfully, passes all checks, and only fails at runtime when a HyperCore user attempts to bridge. [6](#0-5) 

---

### Recommendation

1. **Add a post-deployment setter** on `HyperliquedBridgeToken` restricted to `onlyOwner`:
   ```solidity
   function setSystemAddress(address systemAddress_) external onlyOwner {
       _systemAddress = systemAddress_;
   }
   ```
2. **Or** override `deployToken()` in a Hyperliquid-specific `OmniBridge` subcontract to call the 5-arg `HyperliquedBridgeToken.initialize()` selector instead of `BridgeToken.initialize.selector`.
3. **Or** expose a two-step initialization pattern: deploy the proxy with the 3-arg call, then immediately call a separate `initHyperCore(address systemAddress_, address hyperCoreDeployer_)` function (guarded by `onlyOwner` and a "not yet initialized" flag).

---

### Proof of Concept

1. Admin sets `tokenImplementationAddress = address(new HyperliquedBridgeToken())` in `OmniBridge`.
2. Anyone calls `OmniBridge.deployToken(signatureData, metadata)` with a valid MPC signature.
3. `OmniBridge` deploys `ERC1967Proxy(tokenImplementationAddress, abi.encodeWithSelector(BridgeToken.initialize.selector, name, symbol, decimals))`.
4. The proxy's `_systemAddress` slot is `address(0)`; `initializer` flag is set — the 5-arg `initialize()` is now permanently locked out.
5. A HyperCore user sends tokens via `sendToEvmWithData`; the HyperLiquid system address calls `coreReceiveWithData()` on the proxy.
6. `msg.sender != address(0)` → `revert NotSystemAddress()` — every call reverts, user funds are irrecoverably stuck. [3](#0-2) [1](#0-0)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-195)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
        uint8 decimals = _normalizeDecimals(metadata.decimals);

        // slither-disable-next-line reentrancy-no-eth
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );

        deployTokenExtension(
            metadata.token,
            bridgeTokenProxy,
            decimals,
            metadata.decimals
        );

        emit BridgeTypes.DeployToken(
            bridgeTokenProxy,
            metadata.token,
            metadata.name,
            metadata.symbol,
            decimals,
            metadata.decimals
        );

        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);

        return bridgeTokenProxy;
    }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L32-141)
```text
contract HyperliquedBridgeToken is BridgeToken, ICoreReceiveWithData {
    using SafeCast for uint256;

    address internal _systemAddress;
    bytes32 constant HYPER_CORE_DEPLOYER_SLOT = keccak256("HyperCore deployer");
    event HyperCoreDeployerSet(address indexed deployer);

    uint8 public constant ACTION_TRANSFER = 0;
    uint8 public constant ACTION_INIT_TRANSFER = 1;

    event CoreReceived(
        address indexed sender,
        uint8 indexed action,
        uint256 amount,
        bytes data
    );

    error NotSystemAddress();
    error EmptyActionData();
    error UnknownAction(uint8 action);

    function initialize(
        string memory name_,
        string memory symbol_,
        uint8 decimals_,
        address systemAddress_,
        address hyperCoreDeployer_
    ) external initializer {
        __ERC20_init(name_, symbol_);
        __UUPSUpgradeable_init();
        __Ownable_init(_msgSender());

        _name = name_;
        _symbol = symbol_;
        _decimals = decimals_;
        _systemAddress = systemAddress_;

        bytes32 hyperCoreDeployerSlot = HYPER_CORE_DEPLOYER_SLOT;
        assembly {
            sstore(hyperCoreDeployerSlot, hyperCoreDeployer_)
        }
        emit HyperCoreDeployerSet(hyperCoreDeployer_);
    }

    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external override onlyOwner {
        _mint(account, value);
        _update(account, _systemAddress, value);
    }

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
    /// - data == 0x01 || abi.encode(uint128 fee, string recipient, string message):
    ///   move `amount` from the pool to this contract, then bridge via
    ///   OmniBridge.initTransfer (which burns from `address(this)`). `recipient` is an
    ///   OmniAddress string (e.g. `near:alice.near`, `sol:<base58>`). nativeFee = 0.
    /// The emitted InitTransfer event will carry `sender = address(this)`; the NEAR
    /// side cannot recover the originating HyperCore user (`from`) from this path.
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
        if (data.length == 0) revert EmptyActionData();

        uint8 action = uint8(data[0]);
        bytes calldata tail = data[1:];

        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
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
        } else {
            revert UnknownAction(action);
        }

        emit CoreReceived(from, action, amount, data);
    }
```
