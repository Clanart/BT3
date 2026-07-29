Confirmed: when `PermissionlessRegistration` param is enabled, `RegisterERC20` in `x/erc20/keeper/msg_server.go` explicitly allows **any account to permissionlessly register a native ERC20 contract** to map to a Cosmos coin, with no requirement that the ERC20 contract's `MINTER_ROLE` be revoked or transferred to the module.

### Title
Unlimited native-coin inflation via permissionless registration of externally-mintable ERC20 contracts - (File: x/erc20/keeper/msg_server.go, x/erc20/keeper/proposals.go)

### Summary
`RegisterERC20` [1](#0-0)  permits any user to register an arbitrary, already-deployed ERC20 contract as a "native ERC20" token pair when `params.PermissionlessRegistration` is enabled, giving it `OWNER_EXTERNAL` [2](#0-1)  without ever checking who controls minting on that contract. The conversion flow (`ConvertERC20`/`convertERC20IntoCoinsForNativeToken`) mints bank-module Cosmos coins 1:1 against ERC20 tokens escrowed in the module account [3](#0-2) , implicitly assuming the ERC20 contract's total supply is fixed/controlled. If the registered contract retains a live `MINTER_ROLE` holder — e.g. deployed from the reference `ERC20MinterBurnerDecimals.sol` template where the deployer keeps `MINTER_ROLE` [4](#0-3)  — that minter can freely call `mint()` [5](#0-4)  to create arbitrary ERC20 supply out of thin air and then convert it into native Cosmos coins via `MsgConvertERC20`, producing unbacked bank-module coins.

### Finding Description
This is a direct structural analog to the reported bug class: RuniverseLand enforced a supply cap only through one authorized path (`RuniverseLandMinter`) while a second, unrestricted minter (`secondaryMinter`) could bypass it and mint arbitrary token IDs, breaking the `plotsAvailablePerSize` invariant. Here, `x/erc20`'s bank-coin supply is meant to be strictly backed 1:1 by tokens escrowed from `convertERC20IntoCoinsForNativeToken`, but the actual supply-control point — the ERC20 contract's own `MINTER_ROLE` — is left completely outside module control for `OWNER_EXTERNAL` pairs. The module only verifies that its *own* escrow balance increases correctly [6](#0-5)  before minting native coins; it never checks whether the underlying ERC20 total supply is fixed, whether the module is the sole minter, or whether the registered contract even matches the "no arbitrary minter" assumption that the accounting model requires.

### Impact Explanation
Any address holding `MINTER_ROLE` on a permissionlessly-registered ERC20 contract (which for the reference token template is retained by the original deployer/registrant) can:
1. Call `mint(to, amount)` on their own ERC20 contract to create unlimited ERC20 balance.
2. Submit `MsgConvertERC20` to escrow those freshly minted tokens and receive an equal amount of newly minted native Cosmos-SDK coin from the bank module [7](#0-6) .
3. Use, transfer, or bridge (via IBC) the resulting native coin as spendable value, and/or convert other IBC/ERC20 pairs against it.

This is unauthorized, irreversible minting of spendable native-chain value with no cap, directly matching the "Critical unauthorized minting... irreversible accounting corruption of spendable user value across native balances... IBC escrows" impact category.

### Likelihood Explanation
The trigger requires only an unprivileged user: deploy (or reuse) an ERC20 contract that keeps a controllable `MINTER_ROLE`, call `MsgRegisterERC20` (permissionless when `PermissionlessRegistration=true`), mint tokens to self, and call `MsgConvertERC20`. All steps are ordinary transaction/message flows with no admin, validator, or relayer privilege required. The only condition is that `params.PermissionlessRegistration` is enabled — this is a configurable chain parameter, not an inherent privilege check on the attacker, and governance enabling this feature is intended to allow open registration of arbitrary tokens, which is exactly what makes the exploit reachable.

### Recommendation
- For `OWNER_EXTERNAL` (native ERC20) pairs, require verification (at registration time and/or continuously) that the ERC20 contract cannot mint new supply outside of the module's controlled escrow/unescrow flow — e.g., require renouncing/burning `MINTER_ROLE` to the module, or restrict permissionless registration to contracts that provably have no privileged minter (e.g., simple fixed-supply ERC20s), rejecting `AccessControl`-based mintable tokens.
- Alternatively, track and enforce total-supply invariants: before minting Cosmos coins in `convertERC20IntoCoinsForNativeToken`, verify `totalSupply()` has not grown beyond what the module's escrow accounting expects, or cap conversions by validating that the token's mint authority equals a burned/zero address or the module account.
- Consider disabling `PermissionlessRegistration` by default and clearly documenting/gating this feature given its high-severity blast radius.

### Proof of Concept
1. Deploy `ERC20MinterBurnerDecimals`-style contract (attacker retains `MINTER_ROLE`) — reference implementation: [4](#0-3) .
2. Chain has `params.PermissionlessRegistration = true`; attacker calls `MsgRegisterERC20` with their contract address — handled by [8](#0-7) , creating an `OWNER_EXTERNAL` pair [9](#0-8) .
3. Attacker calls `mint(attacker, hugeAmount)` on the ERC20 contract using their retained `MINTER_ROLE` [5](#0-4) .
4. Attacker submits `MsgConvertERC20` for `hugeAmount`; the module escrows the freshly-minted tokens and mints an equal amount of native Cosmos coin to the attacker's account [10](#0-9) .
5. Attacker now holds `hugeAmount` of unbacked native coin, spendable/transferable/bridgeable like any legitimate balance.

**Note on verification limits:** I was unable to fully trace whether any additional governance-only gate (e.g., a separate allow-list check before `PermissionlessRegistration` takes effect, or extra validation in `CreateCoinMetadata`) further restricts which contracts qualify, since I could not view the full contents of `CreateCoinMetadata` or confirm the default value of `PermissionlessRegistration` in this session. A Devin session with full repository access should verify these details before treating this as conclusively exploitable in the default configuration.

### Citations

**File:** x/erc20/keeper/msg_server.go (L86-140)
```go
	// Escrow tokens on module account
	transferData, err := erc20.Pack("transfer", types.ModuleAddress, msg.Amount.BigInt())
	if err != nil {
		return nil, err
	}

	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}

	// Mint coins
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-361)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeRegisterERC20,
				sdk.NewAttribute(types.AttributeKeyCosmosCoin, pair.Denom),
				sdk.NewAttribute(types.AttributeKeyERC20Token, pair.Erc20Address),
			),
		)
	}

	return &types.MsgRegisterERC20Response{}, nil
```

**File:** x/erc20/keeper/proposals.go (L16-41)
```go
// RegisterERC20 creates a Cosmos coin and registers the token pair between the
// coin and the ERC20
func (k Keeper) registerERC20(
	ctx sdk.Context,
	contract common.Address,
) (*types.TokenPair, error) {
	// Check if ERC20 is already registered
	if k.IsERC20Registered(ctx, contract) {
		return nil, errorsmod.Wrapf(
			types.ErrTokenPairAlreadyExists, "token ERC20 contract already registered: %s", contract.String(),
		)
	}

	metadata, err := k.CreateCoinMetadata(ctx, contract)
	if err != nil {
		return nil, errorsmod.Wrap(
			err, "failed to create wrapped coin denom metadata for ERC20",
		)
	}

	pair := types.NewTokenPair(contract, metadata.Name, types.OWNER_EXTERNAL)
	err = k.SetToken(ctx, pair)
	if err != nil {
		return nil, err
	}
	return &pair, nil
```

**File:** contracts/solidity/ERC20MinterBurnerDecimals.sol (L26-46)
```text
contract ERC20MinterBurnerDecimals is Context, AccessControlEnumerable, ERC20Burnable, ERC20Pausable {
  bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");
  bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");
  bytes32 public constant BURNER_ROLE = keccak256("BURNER_ROLE");
  uint8 private _decimals;

  /**
    * @dev Grants `DEFAULT_ADMIN_ROLE`, `MINTER_ROLE` and `PAUSER_ROLE` to the
    * account that deploys the contract and customizes tokens decimals
    *
    * See {ERC20-constructor}.
    */
  constructor(string memory name, string memory symbol, uint8 decimals_)
    ERC20(name, symbol) {
      _setupRole(DEFAULT_ADMIN_ROLE, _msgSender());

      _setupRole(MINTER_ROLE, _msgSender());
      _setupRole(PAUSER_ROLE, _msgSender());
      _setupRole(BURNER_ROLE, _msgSender());
      _setupDecimals(decimals_);
  }
```

**File:** contracts/solidity/ERC20MinterBurnerDecimals.sol (L71-74)
```text
  function mint(address to, uint256 amount) public virtual {
      require(hasRole(MINTER_ROLE, _msgSender()), "ERC20MinterBurnerDecimals: must have minter role to mint");
      _mint(to, amount);
  }
```
