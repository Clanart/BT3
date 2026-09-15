This confirms the analog. `ServiceChainToken` inherits `ERC20`, `ERC20Mintable`, `ERC20Burnable`, `ERC20ServiceChain` [1](#0-0) , and `ERC20Burnable.burn()` is a public function that any token holder can call to destroy their own tokens, with no bridge-only restriction [2](#0-1) . The only bridge-mediated exit path is `requestValueTransfer`, which transfers tokens to the bridge before the bridge internally calls `ERC20Burnable(_tokenAddress).burn(_value)` on its own balance [3](#0-2) [4](#0-3) .

### Title
Public `ERC20Burnable.burn()` on `ServiceChainToken` lets users destroy mint/burn-mode bridge tokens without unlocking counterpart value - (File: contracts/testing/sc_erc20/sc_token.sol)

### Summary
When a `Bridge` is deployed with `modeMintBurn = true` [5](#0-4) , tokens on the local chain are minted 1:1 against value locked/tracked on the counterpart chain. `ServiceChainToken`, the reference mint/burn-mode ERC20 used with this bridge, inherits the standard OpenZeppelin `ERC20Burnable` contract, whose `burn(uint256 amount)` function is `public` and callable by any token holder on their own balance, with no restriction to only the bridge contract [1](#0-0) [2](#0-1) .

### Finding Description
The intended exit path for a mint/burn-mode token is `ERC20ServiceChain.requestValueTransfer`, which transfers the user's tokens to the bridge and calls `IERC20BridgeReceiver(bridge).onERC20Received(...)`, which in turn invokes the internal `_requestERC20Transfer` that burns the tokens *from the bridge's own balance* only after emitting `RequestValueTransfer` (the event the counterpart bridge operators watch to release/mint the corresponding value on the other chain) [3](#0-2) [6](#0-5) .

Because `burn()` is inherited unmodified from `ERC20Burnable`, any holder can instead call `token.burn(amount)` directly on their own balance, bypassing the bridge entirely. This destroys the token supply on the local chain without emitting `RequestValueTransfer` and without any corresponding release of the locked/tracked value on the counterpart chain, exactly mirroring the reported `L2ECO.burn()` bug class where a token's self-burn capability inherited from a base implementation lets bridged funds be permanently locked out of sync with the destroyed side's supply.

### Impact Explanation
Any unprivileged holder of a mint/burn-mode `ServiceChainToken` can unilaterally reduce the local-chain circulating supply while the counterpart chain's corresponding value remains locked/untouched forever, since there is no bridge mechanism to detect or reconcile a direct `burn()` call. This creates a permanent supply mismatch (peg divergence) between the two chains for that token, and the value corresponding to the burned tokens becomes unrecoverable, a concrete loss of value and unauthorized change in cross-chain token supply parity — satisfying the "state divergence" / value-loss impact bar for Medium severity.

### Likelihood Explanation
High likelihood: this requires only a single, ordinary transaction from any token holder invoking the standard, publicly documented `burn(uint256)` selector (`0x42966c68`) on the `ServiceChainToken` contract; no special privileges, timing, or coordination with the bridge is required.

### Recommendation
Override `burn`/`burnFrom` in the mint-burn-mode token (or in a wrapper used with the bridge) to restrict callers to the registered `bridge` address only (e.g., `require(msg.sender == bridge, "only bridge may burn")`), analogous to restricting `L2ECO.burn` to a burner role rather than allowing arbitrary self-burn, so that supply reduction on one chain always happens through the bridge's `RequestValueTransfer`/`HandleValueTransfer` accounting that keeps both chains' balances synchronized.

### Proof of Concept
1. Deploy `Bridge` with `modeMintBurn = true` and `ServiceChainToken` bound to it, then grant the bridge `minter` role, matching `deployBridgeConfiguredMintBurnFixture` [7](#0-6) .
2. A user holding tokens (e.g., `user1` after `token.connect(owner).transfer(user1.address, parseEther("5.0"))` [8](#0-7) ) calls `token.connect(user1).burn(parseEther("5.0"))` directly instead of `bridge`/`requestValueTransfer`.
3. `ERC20Burnable.burn` succeeds, reducing `user1`'s balance and total supply on this chain [2](#0-1) , with no `RequestValueTransfer` event emitted and no message sent to the counterpart bridge, so the corresponding value on the counterpart chain remains locked with no way to be released — permanently desynchronizing the two chains' token supplies.

### Citations

**File:** contracts/testing/sc_erc20/sc_token.sol (L27-37)
```text
contract ServiceChainToken is ERC20, ERC20Mintable, ERC20Burnable, ERC20ServiceChain {
    string public constant NAME = "ServiceChainToken";
    string public constant SYMBOL = "SCT";
    uint8 public constant DECIMALS = 18;

    // one billion in initial supply
    uint256 public constant INITIAL_SUPPLY = 1000000000 * (10 ** uint256(DECIMALS));

    constructor(address _bridge) ERC20ServiceChain(_bridge) public {
        _mint(msg.sender, INITIAL_SUPPLY);
    }
```

**File:** contracts/libs/openzeppelin-contracts-v2/contracts/token/ERC20/ERC20Burnable.sol (L16-18)
```text
    function burn(uint256 amount) public {
        _burn(msg.sender, amount);
    }
```

**File:** contracts/testing/sc_erc20/ERC20ServiceChain.sol (L44-47)
```text
    function requestValueTransfer(uint256 _amount, address _to, uint256 _feeLimit, bytes calldata _extraData) external {
        require(transfer(bridge, _amount.add(_feeLimit)), "requestValueTransfer: transfer failed");
        IERC20BridgeReceiver(bridge).onERC20Received(msg.sender, _to, _amount, _feeLimit, _extraData);
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L76-108)
```text
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

**File:** contracts/service_chain/bridge/BridgeTransfer.sol (L27-48)
```text
    bool public modeMintBurn = false;
    bool public isRunning = true;

    uint64 public requestNonce; // the number of value transfer request that this contract received.
    uint64 public lowerHandleNonce; // a minimum nonce of a value transfer request that will be handled.
    uint64 public upperHandleNonce; // a maximum nonce of the counterpart bridge's value transfer request that is handled.
    uint64 public recoveryBlockNumber = 1; // the block number that recovery start to filter log from.
    mapping(uint64 => uint64) public handleNoncesToBlockNums;  // <request nonce> => <request blockNum>

    event RunningStatusChanged(bool _status);

    using SafeMath for uint256;

    enum TokenType {
        KLAY,
        ERC20,
        ERC721
    }

    constructor(bool _modeMintBurn) BridgeFee(address(0)) internal {
        modeMintBurn = _modeMintBurn;
    }
```

**File:** contracts/test/Bridge/bridge.test.ts (L71-75)
```typescript
  await ethers.provider.send("hardhat_setBalance", [user1.address, parseEther("5.0").toHexString()]);
  await ethers.provider.send("hardhat_setBalance", [user2.address, "0x0"]);
  await ethers.provider.send("hardhat_setBalance", [feeReceiver.address, "0x0"]);
  await token.connect(owner).transfer(user1.address, parseEther("5.0"));
  await nft.connect(owner).mintWithTokenURI(user1.address, 42, "example.com");
```

**File:** contracts/test/Bridge/bridge.test.ts (L80-88)
```typescript
async function deployBridgeConfiguredMintBurnFixture() {
  const fixture = await deployBridgeConfiguredFixture(true);
  const { bridge, owner, token, nft } = fixture;

  token.connect(owner).addMinter(bridge.address);
  nft.connect(owner).addMinter(bridge.address);

  return fixture;
}
```
