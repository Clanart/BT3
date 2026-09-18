### Title
Unchecked ERC20 transfer return value in the CW20↔ERC20 wrapper allows a non-reverting ERC20 to fake a "successful" transfer, corrupting `Cw20ReceiveMsg` notifications to receiver contracts - ([File: example/cosmwasm/cw20/src/contract.rs])

### Summary
The CosmWasm "CW20 wraps ERC20" bridge contract builds an `EvmMsg::DelegateCallEvm` carrying the ABI-encoded ERC20 `transfer`/`transferFrom` calldata, but never inspects the boolean return value of that call. For any ERC20 token that signals a failed transfer by returning `false` instead of reverting (a widely-used, non-compliant-but-legal ERC20 pattern), the wrapper contract treats the operation as fully successful — it unconditionally emits `action`/`from`/`to`/`amount` attributes and, in the `send`/`send_from` path, dispatches a `Cw20ReceiveMsg` to the destination contract claiming the funds were received, even though no ERC20 balance actually moved.

### Finding Description
The wrapper's core `transfer`/`transfer_from` helpers build the delegatecall payload and immediately return a success `Response`, without decoding/validating the ABI-encoded `bool` that `transfer`/`transferFrom` returns: [1](#0-0) [2](#0-1) 

The `send`/`send_from` entry points compound this by forwarding a `Cw20ReceiveMsg` (with the same `amount`) to the destination contract right after calling the same unchecked `transfer`: [3](#0-2) [4](#0-3) 

On the EVM side, the `MsgInternalEVMDelegateCall` handler (`HandleInternalEVMDelegateCall` → `CallEVM`) only surfaces an error when the EVM execution itself reverts (`res.Err != nil`); it happily returns `res.ReturnData` — the raw ABI-encoded `false` — as a "successful" call result whenever the callee returns normally: [5](#0-4) [6](#0-5) 

This is the same root cause described in H02: a bridge component initiates a token movement and treats the operation as final without checking whether the underlying ERC20 transfer actually succeeded, which breaks synchronization for ERC20 contracts that signal failure via a `false` return value rather than reverting.

By contrast, the analogous native-token ERC20 wrapper *does* check the precompile's boolean return and reverts on failure, showing this is the expected/secure pattern elsewhere in the codebase: [7](#0-6) 
and the EVM-side CW20/CW721 ERC-pointer contracts also `require(success, ...)` on their delegatecall into the wasmd precompile: [8](#0-7) 

### Impact Explanation
Because `send`/`send_from` forward a `Cw20ReceiveMsg` carrying the claimed `amount` to the target CW contract regardless of whether the ERC20 transfer actually succeeded, any CW20-receiver contract that trusts the `amount` field in `Cw20ReceiveMsg` (e.g., a DEX, vault, or staking contract crediting deposits) can be made to believe it received tokens that were never actually transferred. This is an unauthorized-crediting / fund-loss vector reachable purely through CosmWasm message calls against the CW20↔ERC20 wrapper — no privileged access required beyond deploying/using a non-reverting ERC20 as the wrapped token.

### Likelihood Explanation
Exploitation requires the underlying "wrapped" ERC20 to return `false` (rather than revert) on a failed transfer — a legal, if non-standard, ERC20 behavior that a malicious contract deployer fully controls when creating the token to be wrapped. Since contract deployers are an in-scope actor and CW20-wrapped-ERC20 tokens are a first-class pointer mechanism (`x/evm/artifacts/cw20`) used to let ERC20 assets circulate on the CosmWasm side, an attacker can deploy such a token, wrap it, and target any CW20-receiver contract that acts on `Cw20ReceiveMsg.amount` without independently verifying the ERC20 balance change.

### Recommendation
In `transfer`/`transfer_from` (and by extension `execute_send`/`execute_send_from`) in `example/cosmwasm/cw20/src/contract.rs`, decode the ABI-encoded boolean returned by the ERC20 `transfer`/`transferFrom` call (available via the `MsgInternalEVMDelegateCall` result) and return a `ContractError` if it is `false`, instead of unconditionally building a success `Response`. This mirrors the `require(success, ...)` pattern already used by the pointer contracts in `contracts/src/CW20ERC20Pointer.sol` and `contracts/src/CW721ERC721Pointer.sol`.

### Proof of Concept
1. As a contract deployer, deploy a custom ERC20 whose `transfer`/`transferFrom` return `false` (without reverting) whenever the sender's balance is insufficient (this is spec-compliant ERC20 behavior).
2. Register this ERC20 with the CW20 wrapper contract (`example/cosmwasm/cw20`), obtaining a CW20 wrapper contract address.
3. Deploy (or target) a CW20-receiver contract that implements `receive` and credits the sender based on `Cw20ReceiveMsg.amount` without querying the wrapper's `balance` afterward.
4. Call `Send { contract: <receiver>, amount: <more than the sender's actual ERC20 balance>, msg }` on the CW20 wrapper.
5. The wrapper's `transfer` helper issues the ERC20 `transfer` delegatecall, which returns `false` (insufficient balance) but does not revert; `HandleInternalEVMDelegateCall`/`CallEVM` treats this as a successful call since no VM error occurred.
6. The wrapper still emits a successful `Response` and dispatches `Cw20ReceiveMsg{ amount: <requested> }` to the receiver, which credits the sender for tokens that were never actually transferred.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L95-114)
```rust
pub fn execute_send(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    contract: String,
    amount: Uint128,
    msg: Binary,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer(deps, _env, info.clone(), contract.clone(), amount)?;
    let send = Cw20ReceiveMsg {
        sender: info.sender.to_string(),
        amount: amount.clone(),
        msg,
    };

    res = res
        .add_message(cw20receive_into_cosmos_msg(contract.clone(), send)?)
        .add_attribute("action", "send");
    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L205-225)
```rust
pub fn execute_send_from(
    deps: DepsMut<EvmQueryWrapper>,
    env: Env,
    info: MessageInfo,
    owner: String,
    contract: String,
    amount: Uint128,
    msg: Binary,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer_from(deps, env, info.clone(), owner, contract.clone(), amount)?;
    let send = Cw20ReceiveMsg {
        sender: info.sender.to_string(),
        amount: amount.clone(),
        msg,
    };

    res = res
        .add_message(cw20receive_into_cosmos_msg(contract.clone(), send)?)
        .add_attribute("action", "send_from");
    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L227-248)
```rust
fn transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_payload(recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", info.sender)
        .add_attribute("to", recipient)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L250-274)
```rust
pub fn transfer_from(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    owner: String,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_from_payload(owner.clone(), recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", owner)
        .add_attribute("to", recipient)
        .add_attribute("by", info.sender)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** x/evm/keeper/evm.go (L47-77)
```go
func (k *Keeper) HandleInternalEVMDelegateCall(ctx sdk.Context, req *types.MsgInternalEVMDelegateCall) (*sdk.Result, error) {
	var to *common.Address
	if req.To != "" {
		addr := common.HexToAddress(req.To)
		to = &addr
	} else {
		return nil, errors.New("cannot use a CosmWasm contract to delegate-create an EVM contract")
	}
	addr, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(req.FromContract))))
	if !exists || common.BytesToAddress(addr).Cmp(*to) != 0 {
		return nil, errors.New("only pointer contract can make delegatecalls")
	}
	zeroInt := sdk.ZeroInt()
	senderAddr, err := sdk.AccAddressFromBech32(req.Sender)
	if err != nil {
		return nil, err
	}
	// delegatecall caller must be associated; otherwise any state change on EVM contract will be lost
	// after they asssociate.
	senderEvmAddr, found := k.GetEVMAddress(ctx, senderAddr)
	if !found {
		err := types.NewAssociationMissingErr(req.Sender)
		evmKeeperMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "evm_handle_internal_evm_delegate_call"), attribute.String("type", err.AddressType())))
		return nil, err
	}
	ret, err := k.CallEVM(ctx, senderEvmAddr, to, &zeroInt, req.Data)
	if err != nil {
		return nil, err
	}
	return &sdk.Result{Data: ret}, nil
}
```

**File:** x/evm/keeper/evm.go (L112-123)
```go
	res, err := k.applyEVMMessage(ctx, evmMsg, stateDB, gp, false)
	if err != nil {
		return nil, err
	}
	k.consumeEvmGas(ctx, res.UsedGas)
	if res.Err != nil {
		return nil, res.Err
	}
	surplus, err := stateDB.Finalize()
	if err != nil {
		return nil, err
	}
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L45-49)
```text
    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
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
