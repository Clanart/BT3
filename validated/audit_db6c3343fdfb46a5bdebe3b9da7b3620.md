### Title
`CW721ERC721Pointer` never overrides `safeTransferFrom`, so the standard-recommended NFT transfer function always reverts and can permanently block NFT transfers - ([File: contracts/src/CW721ERC721Pointer.sol])

### Summary
`CW721ERC721Pointer` is the EVM-side pointer contract that lets EVM callers interact with a CosmWasm CW721 collection as if it were a normal ERC721 token [1](#0-0) . It overrides `transferFrom`, `approve`, and `setApprovalForAll` to proxy the call into the underlying CW721 contract via the wasmd precompile [2](#0-1) , but it never overrides either `safeTransferFrom` variant inherited from OpenZeppelin's `ERC721`. Because token ownership for this pointer is entirely delegated to the CW721 contract (no `_mint` is ever called on the local OpenZeppelin storage), calling the inherited `safeTransferFrom` invokes OpenZeppelin's internal transfer logic against a token that was never minted in local storage, causing it to always revert.

### Finding Description
`CW721ERC721Pointer` inherits from OpenZeppelin's `ERC721` [3](#0-2) . All state-reading functions (`ownerOf`, `balanceOf`, `getApproved`, `isApprovedForAll`, `tokenURI`) are overridden to query the real CW721 contract state through `WasmdPrecompile.query` [4](#0-3) , and the mutating functions `transferFrom`, `approve`, and `setApprovalForAll` are overridden to execute the corresponding CW721 message (`transfer_nft`, `approve`, `approve_all`/`revoke_all`) through `_execute` [5](#0-4) .

However, the constructor never calls `_mint`, so the base OpenZeppelin `ERC721` contract's own internal `_owners`/`_balances`/`_tokenApprovals` storage is permanently empty for every token ID [6](#0-5) . Since `safeTransferFrom(address,address,uint256)` and `safeTransferFrom(address,address,uint256,bytes)` are not overridden, calls to them fall through to OpenZeppelin's default implementation, which transfers ownership using the contract's own internal storage (via `_transfer`/`_update`) rather than delegating to the CW721 backend. Because no token is ever recorded as minted/owned in that internal storage, the default implementation's ownership check fails and the call reverts for every token ID, every time.

This is essentially the inverse of the referenced report's concern (`transferFrom` used instead of `safeTransferFrom`): here, the contract exposes both functions per the `IERC721` interface, but only `transferFrom` is functional. Any caller — wallet, marketplace, or contract — that follows the standard/recommended practice of using `safeTransferFrom` (exactly the practice the external report advocates for) will find NFT transfers through this pointer permanently broken.

### Impact Explanation
Marketplaces, wallets, and other composability integrations widely default to `safeTransferFrom` for ERC721 transfers precisely because it is the interface-recommended, safer method (as noted in the external report). Because `CW721ERC721Pointer.safeTransferFrom` always reverts, any counterparty relying on it is unable to move a CW721-backed NFT through the EVM pointer using the standard-safe method, effectively freezing NFTs from the perspective of `safeTransferFrom`-only integrations (e.g., contracts that only implement `safeTransferFrom`, or that require `onERC721Received` hooks such as vaults, staking contracts, and marketplaces). Since `transferFrom` and `safeTransferFrom` are both part of the mandatory `IERC721` interface that this pointer claims to support via `supportsInterface` [7](#0-6) , callers have no on-chain way to detect that only half of the interface is functional.

### Likelihood Explanation
Every CW721 collection on Sei can have a corresponding `CW721ERC721Pointer` created via `RegisterPointer` [8](#0-7) , making this reachable by any user who registers or already interacts with a pointer for an existing CW721 collection. Any unprivileged EVM transaction sender or contract that calls `safeTransferFrom` on such a pointer (a completely standard, expected interaction) will hit this bug deterministically, with no special conditions required.

### Recommendation
Override both `safeTransferFrom(address,address,uint256)` and `safeTransferFrom(address,address,uint256,bytes)` in `CW721ERC721Pointer` to route through the same CW721 `transfer_nft` execution path used by `transferFrom` (and perform the `onERC721Received` check against `to` when it is a contract), rather than relying on OpenZeppelin's default implementation that operates on unused local storage.

### Proof of Concept
1. Register a CW721->ERC721 pointer for an existing CW721 collection via `MsgRegisterPointer` (`PointerType_ERC721`) [9](#0-8) .
2. As the owner of `tokenId` in the CW721 collection (verified via `ownerOf` on the pointer, which correctly queries the CW721 contract) [10](#0-9) , call `pointer.safeTransferFrom(from, to, tokenId)`.
3. The call reverts, because it resolves to OpenZeppelin's default `safeTransferFrom`, which checks/updates the pointer contract's own local `ERC721` storage where `tokenId` was never minted (the constructor never calls `_mint`) [6](#0-5) , in contrast to `transferFrom`, which is explicitly overridden and correctly proxies to the CW721 `transfer_nft` execute message [11](#0-10) .

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L4-32)
```text
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "@openzeppelin/contracts/utils/Strings.sol";
import {IERC165} from "@openzeppelin/contracts/utils/introspection/IERC165.sol";
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";

contract CW721ERC721Pointer is ERC721,ERC2981 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw721Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;

    error NotImplementedOnCosmwasmContract(string method);
    error NotImplemented(string method);

    constructor(string memory Cw721Address_, string memory name_, string memory symbol_) ERC721(name_, symbol_) {
        WasmdPrecompile = IWasmd(WASMD_PRECOMPILE_ADDRESS);
        JsonPrecompile = IJson(JSON_PRECOMPILE_ADDRESS);
        AddrPrecompile = IAddr(ADDR_PRECOMPILE_ADDRESS);
        Cw721Address = Cw721Address_;
    }
```

**File:** contracts/src/CW721ERC721Pointer.sol (L34-40)
```text
    function supportsInterface(bytes4 interfaceId) public pure override(ERC721, ERC2981) returns (bool) {
        return
            interfaceId == type(IERC2981).interfaceId ||
            interfaceId == type(IERC165).interfaceId ||
            interfaceId == type(IERC721).interfaceId ||
            interfaceId == type(IERC721Metadata).interfaceId;
    }
```

**File:** contracts/src/CW721ERC721Pointer.sol (L81-113)
```text
    function ownerOf(uint256 tokenId) public view override returns (address) {
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("owner_of", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes memory owner_ = JsonPrecompile.extractAsBytes(response, "owner");
        return AddrPrecompile.getEvmAddr(string(owner_));
    }

    function getApproved(uint256 tokenId) public view override returns (address) {
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("approvals", _curlyBrace(tId)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes[] memory approvals = JsonPrecompile.extractAsBytesList(response, "approvals");
        if (approvals.length > 0) {
            bytes memory res = JsonPrecompile.extractAsBytes(approvals[0], "spender");
            return AddrPrecompile.getEvmAddr(string(res));
        }
        return address(0);
    }

    function isApprovedForAll(address owner_, address operator) public view override returns (bool) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner_)));
        string memory req = _curlyBrace(_formatPayload("all_operators", _curlyBrace(o)));
        bytes memory response = WasmdPrecompile.query(Cw721Address, bytes(req));
        bytes[] memory approvals = JsonPrecompile.extractAsBytesList(response, "operators");
        for (uint i=0; i<approvals.length; i++) {
            bytes memory op = JsonPrecompile.extractAsBytes(approvals[i], "spender");
            if (AddrPrecompile.getEvmAddr(string(op)) == operator) {
                return true;
            }
        }
        return false;
    }
```

**File:** contracts/src/CW721ERC721Pointer.sol (L159-198)
```text
    // Transactions
    function transferFrom(address from, address to, uint256 tokenId) public override {
        if (to == address(0)) {
            revert ERC721InvalidReceiver(address(0));
        }
        require(from == ownerOf(tokenId), "`from` must be the owner");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("transfer_nft", _curlyBrace(_join(recipient, tId, ","))));
        _execute(bytes(req));
    }

    function approve(address approved, uint256 tokenId) public override {
        string memory spender = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(approved)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("approve", _curlyBrace(_join(spender, tId, ","))));
        _execute(bytes(req));
    }

    function setApprovalForAll(address operator, bool approved) public override {
        string memory op = _curlyBrace(_formatPayload("operator", _doubleQuotes(AddrPrecompile.getSeiAddr(operator))));
        if (approved) {
            _execute(bytes(_curlyBrace(_formatPayload("approve_all", op))));
        } else {
            _execute(bytes(_curlyBrace(_formatPayload("revoke_all", op))));
        }
    }

    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw721Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```

**File:** x/evm/keeper/msg_server.go (L247-313)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
	}
	var existingPointer sdk.AccAddress
	var existingVersion uint16
	var currentVersion uint16
	var exists bool
	switch msg.PointerType {
	case types.PointerType_ERC20:
		currentVersion = erc20.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC721:
		currentVersion = erc721.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC1155:
		currentVersion = erc1155.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	default:
		panic("unknown pointer type")
	}
	if exists && existingVersion >= currentVersion {
		return nil, fmt.Errorf("pointer %s already registered at version %d", existingPointer.String(), existingVersion)
	}
	payload := map[string]interface{}{}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		payload["erc20_address"] = msg.ErcAddress
	case types.PointerType_ERC721:
		payload["erc721_address"] = msg.ErcAddress
	case types.PointerType_ERC1155:
		payload["erc1155_address"] = msg.ErcAddress
	default:
		panic("unknown pointer type")
	}
	codeID := server.GetStoredPointerCodeID(ctx, msg.PointerType)
	moduleAcct := server.accountKeeper.GetModuleAddress(types.ModuleName)
	var err error
	var pointerAddr sdk.AccAddress
	if exists {
		bz, _ := json.Marshal(map[string]interface{}{})
		pointerAddr = existingPointer
		_, err = server.wasmKeeper.Migrate(ctx, existingPointer, moduleAcct, codeID, bz)
	} else {
		bz, jerr := json.Marshal(payload)
		if jerr != nil {
			return nil, jerr
		}
		pointerAddr, _, err = server.wasmKeeper.Instantiate(ctx, codeID, moduleAcct, moduleAcct, bz, fmt.Sprintf("Pointer of %s", msg.ErcAddress), sdk.NewCoins())
	}
	if err != nil {
		return nil, err
	}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		err = server.SetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc20"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc20.CurrentVersion))))
	case types.PointerType_ERC721:
		err = server.SetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc721"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc721.CurrentVersion))))
```
