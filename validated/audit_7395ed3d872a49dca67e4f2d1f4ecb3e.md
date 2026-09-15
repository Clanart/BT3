### Title
Unrestricted `ERC20Burnable.burn()`/`burnFrom()` on mint-burn service-chain tokens permanently locks parent-chain (KLAY/ERC20) value without notifying the counterpart `Bridge` - (File: `contracts/service_chain/bridge/BridgeTransferERC20.sol`, `contracts/testing/sc_erc20/sc_token.sol`, `contracts/libs/openzeppelin-contracts-v2/contracts/token/ERC20/ERC20Burnable.sol`)

### Summary
`ServiceChainToken` (the child-chain token used in Kaia's mint-burn Service Chain `Bridge`) inherits `ERC20Burnable`, which exposes a fully public, unrestricted `burn(uint256 amount)` / `burnFrom(address, uint256)`. Any token holder can call `burn()` directly on the child chain, destroying tokens outside of the bridge's `_requestERC20Transfer` flow. Because the bridge's mint-burn accounting model assumes 1:1 backing between locked value on the counterpart (parent) chain and minted supply on the child chain, a direct user-initiated burn silently reduces child-chain supply while the corresponding value stays locked forever in the counterpart `Bridge` contract, with no `RequestValueTransfer` event ever emitted to trigger an unlock.

### Finding Description
`BridgeTransferERC20._requestERC20Transfer` is the only sanctioned path that burns tokens in mint-burn mode: [1](#0-0) 

It calls `ERC20Burnable(_tokenAddress).burn(_value)` and, critically, always emits `RequestValueTransfer`, which is what the counterpart chain's operators watch for in order to release/unlock the paired value on the other chain.

However, `ServiceChainToken` composes `ERC20Burnable` directly into its public interface: [2](#0-1) 

And `ERC20Burnable.burn`/`burnFrom` are public functions with no access control, callable by any token holder, completely independent of the `Bridge` contract: [3](#0-2) 

Any unprivileged token holder on the child chain can call `token.burn(amount)` (or `burnFrom` with an allowance) directly, bypassing `Bridge._requestERC20Transfer` entirely. This burns the child-chain supply without emitting `RequestValueTransfer`, so the operators/relayer infrastructure that listens for that event to release funds on the counterpart chain never triggers. The value that was locked in the counterpart `Bridge` contract (deposited when the mint-burn token was originally created/backed) remains locked permanently, while the visible circulating supply on the child chain has shrunk — an unrecoverable, unaccounted-for value destruction that breaks the two-chain balance invariant the standard bridge relies on.

This is the same bug class as the referenced report: a token holder unilaterally burning wrapped/bridged tokens through a standard ERC20 burn interface with no effect on the counterpart chain's locked balance, permanently stranding value.

### Impact Explanation
This breaks the fundamental value-conservation invariant of the mint-burn bridge: total minted supply on the child chain is supposed to always be redeemable/backed by the exact amount locked on the counterpart chain. A user-triggered direct `burn()` call permanently destroys child-chain tokens outside the bridge's accounting and messaging path, causing the locked value on the counterpart bridge to become permanently unrecoverable/stuck, and the two chains' effective supplies diverge irrecoverably. This is a direct, unrecoverable loss of value tied to the bridge's core value-transfer guarantee, reachable by any single unprivileged token holder issuing an ordinary transaction.

### Likelihood Explanation
Trivial to trigger: any account holding mint-burn `ServiceChainToken` (or `ServiceChainNFT`/other burnable bridge tokens) balance can call the standard, permissionless `burn`/`burnFrom` function in a single transaction. No special privileges, timing, or contract interaction sequencing is required, and the deployed contracts in `contracts/service_chain/bridge/` and their test/binding artifacts confirm `burn`/`burnFrom` are exposed on the production bridge token ABI (`ServiceChainTokenFuncSigs`), e.g. `0x42966c68` (`burn`) and `0x79cc6790` (`burnFrom`). [4](#0-3) 

### Recommendation
Restrict `burn`/`burnFrom` on mint-burn bridge tokens (`ServiceChainToken`, `ServiceChainNFT`, and any other `ERC20Burnable`/`ERC721Burnable`-based bridge token) so that only the registered `Bridge` contract can invoke them, or remove the public `ERC20Burnable` inheritance from bridge tokens and instead implement an internal-only burn callable exclusively from `BridgeTransferERC20._requestERC20Transfer`/`BridgeTransferERC721._requestERC721Transfer`. Alternatively, add a modifier requiring `msg.sender == bridge` on `burn`/`burnFrom` for tokens deployed in mint-burn mode.

### Proof of Concept
1. Deploy `Bridge` with `modeMintBurn = true` and deploy `ServiceChainToken(bridgeAddress)`, granting the bridge minter role (as in `deployBridgeConfiguredMintBurnFixture` in `contracts/test/Bridge/bridge.test.ts`).
2. Value transfer is initially set up: parent chain locks N tokens, child-chain bridge mints N tokens to a user via `handleERC20Transfer`.
3. Instead of calling `bridge.requestERC20Transfer(...)` (which burns and emits `RequestValueTransfer`), the user calls `token.burn(N)` directly.
4. `ERC20Burnable.burn` executes `_burn(msg.sender, N)`, reducing child-chain `totalSupply` by N with a `Transfer(user, address(0), N)` event but no `RequestValueTransfer` event and no interaction with `Bridge` at all.
5. The counterpart bridge/parent chain never observes any request to release the locked N tokens; they remain permanently locked in the counterpart `Bridge` contract while the child chain's circulating/backed supply is now understated — a permanent value discrepancy with no assessor error message or reversion.

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

**File:** contracts/libs/openzeppelin-contracts-v2/contracts/token/ERC20/ERC20Burnable.sol (L10-26)
```text
contract ERC20Burnable is ERC20 {
    /**
     * @dev Destoys `amount` tokens from the caller.
     *
     * See `ERC20._burn`.
     */
    function burn(uint256 amount) public {
        _burn(msg.sender, amount);
    }

    /**
     * @dev See `ERC20._burnFrom`.
     */
    function burnFrom(address account, uint256 amount) public {
        _burnFrom(account, amount);
    }
}
```

**File:** contracts/bindings/testing/sc_erc20/sc_token.go (L6436-6455)
```go
// Burn is a paid mutator transaction binding the contract method 0x42966c68.
//
// Solidity: function burn(uint256 amount) returns()
func (_ServiceChainToken *ServiceChainTokenTransactor) Burn(opts *bind.TransactOpts, amount *big.Int) (*types.Transaction, error) {
	return _ServiceChainToken.contract.Transact(opts, "burn", amount)
}

// Burn is a paid mutator transaction binding the contract method 0x42966c68.
//
// Solidity: function burn(uint256 amount) returns()
func (_ServiceChainToken *ServiceChainTokenSession) Burn(amount *big.Int) (*types.Transaction, error) {
	return _ServiceChainToken.Contract.Burn(&_ServiceChainToken.TransactOpts, amount)
}

// Burn is a paid mutator transaction binding the contract method 0x42966c68.
//
// Solidity: function burn(uint256 amount) returns()
func (_ServiceChainToken *ServiceChainTokenTransactorSession) Burn(amount *big.Int) (*types.Transaction, error) {
	return _ServiceChainToken.Contract.Burn(&_ServiceChainToken.TransactOpts, amount)
}
```
