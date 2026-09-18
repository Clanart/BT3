### Title
Non-conforming (USDT-style) ERC20 tokens permanently break `increase_allowance`/`decrease_allowance` on the ERC20→CW20 wrapper contract - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CosmWasm `cw20` wrapper contract that adapts an arbitrary ERC20 token to the CW20 interface implements `execute_increase_allowance` and `execute_decrease_allowance` by computing an absolute target allowance and then calling the underlying ERC20's `approve()` function directly, rather than calling `increaseAllowance`/`decreaseAllowance` or resetting to zero first. This reproduces the exact bug class described in the reference report: any underlying ERC20 that requires the allowance to be reset to zero before it can be changed to a new non-zero value (USDT and similar non-conforming tokens) will cause every subsequent allowance-adjustment call to revert once a non-zero allowance already exists.

### Finding Description
`execute_increase_allowance` and `execute_decrease_allowance` query the current on-chain allowance via `EvmQuerier::erc20_allowance`, compute `new_allowance` (current +/- amount), and then build a delegatecall payload with `querier.erc20_approve_payload(spender, new_allowance)` which is sent as a raw EVM `approve(spender, new_allowance)` call to the wrapped ERC20 contract. [1](#0-0) [2](#0-1) 

The payload itself is built on-chain by `HandleERC20ApprovePayload`, which packs a plain `approve(spenderEvmAddr, amount.BigInt())` call — an absolute set, not an increment — and is invoked through the wasm binding switch for `ERC20ApproveType`. [3](#0-2) [4](#0-3) 

For a USDT-style ERC20 that reverts on `approve()` when the current allowance is non-zero and the new value is also non-zero (i.e., it requires resetting to 0 first), any second call to `increase_allowance` or `decrease_allowance` while a non-zero allowance is outstanding will cause the underlying `approve()` delegatecall to revert, which in turn causes the whole CosmWasm execute message to fail (`CosmWasm execute failed` pattern seen throughout the pointer test suite, e.g. `contracts/test/ERC20toCW20PointerTest.js` lines 263, 274, matching the general failure mode of `_execute`/underlying `approve` reverting). [5](#0-4) 

This directly matches the reported bug class: the code changes a non-zero allowance to a different non-zero value without first zeroing it, which non-conforming tokens like USDT reject.

### Impact Explanation
Once a spender has any non-zero allowance approved through this wrapper against a USDT-style underlying ERC20, every future call to `increase_allowance` or `decrease_allowance` for that (owner, spender) pair will permanently revert, because the new target allowance will virtually always be non-zero when the current allowance is also non-zero. This is a denial of service on core CW20 allowance functionality of the wrapper contract for any wrapped non-conforming ERC20 token, freezing the ability of CosmWasm users to adjust/manage their approvals against such wrapped tokens.

### Likelihood Explanation
Any user can permissionlessly instantiate this `cw20` wrapper contract for an arbitrary underlying ERC20 address (`ERC20_ADDRESS` is set freely at instantiation) and interact with it via ordinary `MsgExecuteContract` wasm calls. USDT itself, and any token cloned from its non-conforming `approve` behavior, is a widely used, foreseeable input. No privileged access is required — a single unprivileged wasm message triggers the failure once an allowance is already outstanding.

### Recommendation
In `execute_increase_allowance` and `execute_decrease_allowance` (`example/cosmwasm/cw20/src/contract.rs`), first send an `approve(spender, 0)` payload before sending the payload for the new non-zero `new_allowance`, mirroring the standard `safeIncreaseAllowance`/`safeApprove`-with-reset pattern, so that non-conforming ERC20 tokens (USDT-style) do not permanently break allowance adjustments.

### Proof of Concept
1. Deploy the `example/cosmwasm/cw20` wrapper contract with `ERC20_ADDRESS` pointing to a USDT-style ERC20 contract that reverts on `approve()` unless the current allowance is 0 or the new value is 0.
2. Call `ExecuteMsg::IncreaseAllowance { spender, amount: 100 }` — this succeeds, setting allowance from 0 to 100 (underlying `approve(spender, 100)` on a 0 allowance succeeds).
3. Call `ExecuteMsg::IncreaseAllowance { spender, amount: 50 }` again — `execute_increase_allowance` computes `new_allowance = 150` and calls `erc20_approve_payload(spender, 150)`, which sends `approve(spender, 150)` to the underlying token while its current allowance is 100 (non-zero) — the underlying non-conforming ERC20 reverts, and the CosmWasm execute call fails with `CosmWasm execute failed`, permanently blocking further allowance adjustments for that (owner, spender) pair unless the allowance happens to be manually zeroed out through a separate, unsupported path.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L118-150)
```rust
pub fn execute_increase_allowance(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    spender: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&spender)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);

    // Query the current allowance for this user
    let current_allowance = querier.erc20_allowance(erc_addr.clone(), info.sender.clone().into_string(), spender.clone())?.allowance;

    // Set the new allowance as the sum of the current allowance and amount specified
    let new_allowance = current_allowance + amount;

    // Send the message to approve the new amount
    let payload = querier.erc20_approve_payload(spender.clone(), new_allowance)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };

    let res = Response::new()
        .add_attribute("action", "increase_allowance")
        .add_attribute("spender", spender)
        .add_attribute("amount", amount)
        .add_attribute("new_allowance", new_allowance)
        .add_attribute("by", info.sender)
        .add_message(msg);

    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L152-189)
```rust
// Decrease the allowance of spender by amount.
// Expiration does not work here since it is not supported by ERC20.
pub fn execute_decrease_allowance(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    spender: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&spender)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    // Query the current allowance for this spender
    let querier = EvmQuerier::new(&deps.querier);
    let current_allowance = querier.erc20_allowance(erc_addr.clone(), info.sender.clone().into_string(), spender.clone())?.allowance;

    // If the new allowance after deduction is negative, set allowance to 0.
    let new_allowance = match current_allowance.checked_sub(amount)
    {
        Ok(new_amount) => new_amount,
        Err(_) => Uint128::MIN,
    };
    
    // Send the message to approve the new amount.
    let payload = querier.erc20_approve_payload(spender.clone(), new_allowance)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };

    let res = Response::new()
        .add_attribute("action", "decrease_allowance")
        .add_attribute("spender", spender)
        .add_attribute("amount", amount)
        .add_attribute("new_allowance", new_allowance)
        .add_attribute("by", info.sender)
        .add_message(msg);

    Ok(res)
}
```

**File:** x/evm/client/wasm/query.go (L291-307)
```go
func (h *EVMQueryHandler) HandleERC20ApprovePayload(ctx sdk.Context, spender string, amount *sdk.Int) ([]byte, error) {
	abi, err := native.NativeMetaData.GetAbi()
	if err != nil {
		return nil, err
	}
	spenderEvmAddr, found := h.k.GetEVMAddress(ctx, sdk.MustAccAddressFromBech32(spender))
	if !found {
		return nil, types.NewAssociationMissingErr(spender)
	}

	bz, err := abi.Pack("approve", spenderEvmAddr, amount.BigInt())
	if err != nil {
		return nil, err
	}
	res := bindings.ERCPayloadResponse{EncodedPayload: base64.StdEncoding.EncodeToString(bz)}
	return json.Marshal(res)
}
```

**File:** wasmbinding/queries.go (L152-154)
```go
	case evmbindings.ERC20ApproveType:
		c := parsedQuery.ERC20ApprovePayload
		return qp.evmHandler.HandleERC20ApprovePayload(ctx, c.Spender, c.Amount)
```

**File:** contracts/test/ERC20toCW20PointerTest.js (L255-277)
```javascript
                it("should fail transferFrom() if sender has insufficient balance", async function () {
                    const recipient = admin;
                    const owner = accounts[0];
                    const spender = accounts[1];

                    const tx = await pointer.approve(spender.evmAddress, 999999999);
                    await tx.wait();

                    await expect(pointer.connect(spender.signer).transferFrom(owner.evmAddress, recipient.evmAddress, 999999999)).to.be.revertedWith("CosmWasm execute failed");
                });

                it("should fail transferFrom() if allowance is too low", async function () {
                    const recipient = admin;
                    const owner = accounts[0];
                    const spender = accounts[1];

                    const tx = await pointer.approve(spender.evmAddress, 10, { gasPrice: ethers.parseUnits('100', 'gwei') });
                    await tx.wait();

                    await expect(pointer.connect(spender.signer).transferFrom(owner.evmAddress, recipient.evmAddress, 20, { gasPrice: ethers.parseUnits('100', 'gwei') })).to.be.revertedWith("CosmWasm execute failed");
                    // put it back
                    await (await pointer.approve(spender.evmAddress, 0, { gasPrice: ethers.parseUnits('100', 'gwei') })).wait()
                });
```
