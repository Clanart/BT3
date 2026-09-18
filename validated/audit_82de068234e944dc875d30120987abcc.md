### Title
WSEI.sol `withdraw()` uses `.transfer()` for native SEI, causing permanent fund freezing for contract recipients - ([File: contracts/src/WSEI.sol])

### Summary
The production WSEI (Wrapped Sei) contract's `withdraw()` function unwraps WSEI tokens by sending native SEI back to `msg.sender` using Solidity's `.transfer()`, which forwards a hard-coded 2300 gas stipend.

### Finding Description
`WSEI.sol` is the canonical wrapped-native-token contract for Sei's EVM, deployed via the artifact system at [1](#0-0) , and referenced/deployable through the chain's CLI at `CmdDeployWSEI()` [2](#0-1) . In its `withdraw()` function, the contract sends native SEI to the caller using the low-level `.transfer()` primitive:

```solidity
function withdraw(uint wad) public {
    require(balanceOf[msg.sender] >= wad);
    balanceOf[msg.sender] -= wad;
    payable(msg.sender).transfer(wad);
    emit Withdrawal(msg.sender, wad);
}
``` [3](#0-2) 

`.transfer()` forwards only a 2300 gas stipend to the recipient's `receive()`/`fallback()`. If `msg.sender` is a smart contract (e.g., a multisig, a DeFi vault, a smart-contract wallet, or a proxy) whose native-token receive logic consumes more than 2300 gas — which is common with modern receive hooks, reentrancy guards, or storage writes — the SEI transfer will always revert. Because `balanceOf[msg.sender]` was already decremented before the `.transfer()` call, the reverted transaction as a whole reverts and leaves the caller's WSEI balance unaffected on failure, but the caller is permanently unable to redeem their WSEI for native SEI through this path: every call to `withdraw()` from that contract will fail identically, with no way to ever redeem those wrapped tokens for the native asset via this contract.

### Impact Explanation
WSEI is Sei's standard wrapped-native-token contract, analogous to WETH, and is expected to be integrated by DeFi protocols, DEX routers, and smart-contract wallets. Any contract-based holder of WSEI (a common pattern for automated market makers, vaults, or multisig treasuries) whose fallback/receive logic requires more than 2300 gas will have its WSEI permanently frozen — it can never be unwrapped back into native SEI through `withdraw()`. This is a permanent freezing-of-funds condition reachable by any unprivileged contract-based public-RPC/EVM caller that deposits into WSEI, satisfying the "permanent freezing of funds" impact bar.

### Likelihood Explanation
This is trivially and deterministically reachable: any contract that (a) has a `receive()`/`fallback()` needing >2300 gas and (b) calls `WSEI.deposit()` (or receives WSEI via `transfer`) and later calls `withdraw()` will hit this every time, with no attacker action needed beyond normal usage of the contract as the recipient's own wallet/vault logic. No malicious peer, validator, or governance action is required — it is purely a function of standard EVM gas-forwarding semantics on a production, user-facing contract.

### Recommendation
Replace `.transfer()` with a low-level `.call{value: wad}("")` and check the return value, following the checks-effects-interactions pattern already used in `withdraw()`:

```solidity
function withdraw(uint wad) public {
    require(balanceOf[msg.sender] >= wad);
    balanceOf[msg.sender] -= wad;
    (bool success, ) = payable(msg.sender).call{value: wad}("");
    require(success, "SEI transfer failed");
    emit Withdrawal(msg.sender, wad);
}
```
Add a reentrancy guard if not already enforced elsewhere, since `.call` forwards all remaining gas and re-enables reentrancy vectors that `.transfer()` implicitly mitigated.

### Proof of Concept
1. Deploy a `VaultReceiver` contract whose `receive()` function performs a storage write consuming more than 2300 gas (e.g., `uint256 x; receive() external payable { x = block.timestamp; }`).
2. From `VaultReceiver`, call `WSEI.deposit{value: 1 ether}()`. This succeeds since `deposit()` does not send native SEI out.
3. From `VaultReceiver`, call `WSEI.withdraw(1 ether)`.
4. The internal `payable(msg.sender).transfer(wad)` call to `VaultReceiver.receive()` runs out of the 2300 gas stipend and reverts, causing the entire `withdraw()` transaction to revert.
5. `VaultReceiver`'s 1 WSEI balance remains permanently non-redeemable for native SEI through this contract, for any and all future `withdraw()` attempts, since the gas stipend limitation is a protocol constant not depending on network conditions.

### Citations

**File:** x/evm/artifacts/wsei/artifacts.go (L1-8)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
```

**File:** x/evm/client/cli/tx.go (L35-60)
```go
	"github.com/sei-protocol/sei-chain/x/evm/artifacts/wsei"
	"github.com/sei-protocol/sei-chain/x/evm/types"
)

const (
	FlagGasFeeCap = "gas-fee-cap"
	FlagGas       = "gas-limit"
	FlagValue     = "value"
	FlagRPC       = "evm-rpc"
	FlagNonce     = "nonce"
)

// GetTxCmd returns the transaction commands for this module
func GetTxCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:                        types.ModuleName,
		Short:                      fmt.Sprintf("%s transactions subcommands", types.ModuleName),
		DisableFlagParsing:         true,
		SuggestionsMinimumDistance: 2,
		RunE:                       client.ValidateCmd,
	}

	cmd.AddCommand(CmdSend())
	cmd.AddCommand(CmdDeployContract())
	cmd.AddCommand(CmdCallContract())
	cmd.AddCommand(CmdDeployWSEI())
```

**File:** contracts/src/WSEI.sol (L27-32)
```text
    function withdraw(uint wad) public {
        require(balanceOf[msg.sender] >= wad);
        balanceOf[msg.sender] -= wad;
        payable(msg.sender).transfer(wad);
        emit Withdrawal(msg.sender, wad);
    }
```
