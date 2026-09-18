Based on my research, the pointer registration system in sei-chain does not implement any token whitelist, mirroring the exact bug class described in the Cooler report: any unprivileged caller can register a pointer for an arbitrary ERC20, CW20, ERC721, or ERC1155 token without any restriction on the underlying token's behavior, and downstream code assumes standard, non-fee-on-transfer, non-reentrant token semantics.

### Title
Permissionless CW20/ERC20 pointer registration accepts malicious/fee-on-transfer/reentrant tokens with no whitelist - (File: x/evm/keeper/msg_server.go, precompiles/pointer/pointer.go)

### Summary
`MsgRegisterPointer` and the `Pointer` precompile's `AddCW20`, `AddNative`, `AddCW721`, and `AddCW1155` methods allow any unprivileged sender to permissionlessly register a pointer contract for an arbitrary token address, with no whitelist or validation of the underlying token's transfer semantics.

### Finding Description
`msgServer.RegisterPointer` [1](#0-0)  accepts any `msg.ErcAddress` from any sender and instantiates a pointer contract for it as long as no existing pointer at an equal-or-higher version exists — there is no whitelist check on the token address being pointed to. Likewise, `AddCW20` in the `Pointer` precompile queries `token_info` from an arbitrary CosmWasm contract address supplied by the caller and deploys an ERC20 pointer wrapping it, again with no whitelist: [2](#0-1) . The CLI/EVM tx paths (`register-cw-pointer`, `register-evm-pointer`) expose this same unrestricted registration to any transaction sender [3](#0-2) .

Once registered, the `CW20ERC20Pointer.sol` contract forwards `transfer`/`transferFrom` calls 1:1 to the underlying CW20 contract via the wasmd precompile without any accounting for tokens that do not move value as expected (fee-on-transfer, rebasing, or tokens with malicious/reentrant `execute` handlers) [4](#0-3) . Because the CW20 contract being wrapped can be any arbitrary CosmWasm contract chosen by the registrant (not necessarily a compliant CW20), the pointer blindly assumes the `token_info`/`balance`/`transfer` responses are well-formed and that transferred amounts match the requested amounts — exactly the "weird token" assumption flagged in the original report. The `AddCW20` precompile logic further trusts arbitrary `name`/`symbol` fields returned by the target contract's query response without validation, which are then baked into the deployed pointer's on-chain metadata [5](#0-4) .

### Impact Explanation
Since pointer contracts are relied upon by wallets, DEXs, and other on-chain integrations as ERC20-compliant proxies for CosmWasm tokens (and vice versa), a malicious or non-standard underlying token registered without a whitelist can cause callers relying on `transfer`/`transferFrom` return values or balance deltas to misaccount funds, exactly analogous to the fee-on-transfer scenario in the Cooler report. This can result in unauthorized transfer/accounting mismatches for any protocol built atop these permissionless pointers, and any test/integration harness which treats registered pointer tokens as trustworthy ERC20/CW20 tokens.

### Likelihood Explanation
Likelihood is high: pointer registration is fully permissionless and callable by any transaction sender or EVM contract caller with no gatekeeping beyond gas fees, matching the "MEDIUM likelihood, HIGH impact" characterization of the original finding — anyone can create a pointer for any token, including intentionally malicious ones, with a single transaction.

### Recommendation
Introduce an allow-list/whitelist mechanism (or at minimum stricter validation) in `RegisterPointer`/`AddCW20`/`AddNative` that verifies the target token conforms to expected ERC20/CW20 semantics (e.g., checking transferred amount vs. requested amount, rejecting contracts with unexpected behavior) before permitting pointer creation, or restrict registration to a governance/admin-controlled process for tokens that will be trusted by downstream integrations.

### Proof of Concept
Not independently verified with a live deployment (index-only investigation); the reachable path is: any account submits `MsgRegisterPointer` (or calls the `Pointer` precompile's `addCW20Pointer`/`addNativePointer` from EVM) pointing at a CW20/ERC20 contract they control that implements fee-deducting or malicious transfer logic in its `execute` handler; the resulting pointer contract at `contracts/src/CW20ERC20Pointer.sol` will proxy calls to it without validating that the CosmWasm token actually moved the full requested amount, since `_execute` only checks `success`, not the delta in balances [6](#0-5) .

### Citations

**File:** x/evm/keeper/msg_server.go (L247-271)
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
```

**File:** precompiles/pointer/legacy/v555/pointer.go (L204-219)
```go
func (p Precompile) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
```

**File:** precompiles/pointer/legacy/v555/pointer.go (L220-238)
```go
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	constructorArguments := []interface{}{
		cwAddr, name, symbol,
	}

	packedArgs, err := cw20.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(cw20.GetBin(), packedArgs...)
```

**File:** x/evm/client/cli/native_tx.go (L60-87)
```go
func RegisterCwPointerCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "register-cw-pointer [pointer type] [erc address]",
		Short: `Register a CosmWasm pointer for an ERC20/721/1155 contract. Pointer type is either ERC20, ERC721, or ERC1155.`,
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			msg := &types.MsgRegisterPointer{
				Sender:      clientCtx.GetFromAddress().String(),
				PointerType: types.PointerType(types.PointerType_value[args[0]]),
				ErcAddress:  args[1],
			}
			if err := msg.ValidateBasic(); err != nil {
				return err
			}

			return tx.GenerateOrBroadcastTxCLI(cmd.Context(), clientCtx, cmd.Flags(), msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)

	return cmd
}
```

**File:** contracts/src/CW20ERC20Pointer.sol (L79-96)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```
