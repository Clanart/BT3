### Title
Fixed 2300-gas `.transfer()` in the canonical WSEI contract can permanently freeze wrapped SEI for contract accounts - (File: contracts/src/WSEI.sol)

### Summary
The chain ships a canonical Wrapped-SEI (WSEI) contract whose bytecode/ABI are embedded and deployed by the node itself [1](#0-0) . Its `withdraw` function converts wrapped SEI back to native SEI using Solidity's `transfer`, which forwards only a fixed 2300-gas stipend, exactly the pattern flagged in the external report.

### Finding Description
`WSEI.withdraw` debits the caller's internal balance and then sends native SEI back via `payable(msg.sender).transfer(wad)`: [2](#0-1) 

`transfer()` hard-codes a 2300-gas stipend for the recipient's `receive`/`fallback` execution. Any caller that is itself a contract (a smart-contract wallet, multisig, proxy, or any contract that deposited via `deposit()`/`receive()` at line 20-22) whose `receive`/`fallback` needs more than 2300 gas — e.g., it writes to storage, emits an event with dynamic data, or simply hits a cold `SLOAD`/`SSTORE` post EIP‑2929 gas repricing — will have the internal `CALL` made by `transfer` run out of gas and revert. Because `balanceOf[msg.sender] -= wad` already executed before the `transfer` call, the revert unwinds that state change too, so no funds are lost in a single call — but `withdraw` becomes permanently and unconditionally unusable for that account: there is no alternate withdrawal path, no ability to specify a different payable recipient, and no low-level `call` fallback.

### Impact Explanation
WSEI is deployed by the chain as the canonical wrapped-SEI ERC-20 (embedded ABI/bytecode in `x/evm/artifacts/wsei`) rather than a demo/test contract, so it is reachable by any EVM user or contract that wraps native SEI. Any contract account whose `receive`/`fallback` costs more than 2300 gas can deposit SEI into WSEI but can never withdraw it back — its WSEI balance is permanently stuck with no recovery path, since `transfer` is the only exit mechanism and it always reverts for that caller. This satisfies the "permanent freezing of funds" impact bar.

### Likelihood Explanation
Likelihood is realistic because many common smart-contract wallets and vault/router contracts perform bookkeeping (storage writes, event logging with non-trivial memory) in `receive`/`fallback`, which routinely exceeds 2300 gas, especially after EIP-2929 raised cold `SLOAD` costs to 2100 gas alone. Any such contract that innocently deposits SEI into WSEI (via `deposit()` or the `receive`/`fallback` at lines 17-22) will discover its funds are permanently unrecoverable when it calls `withdraw`.

### Recommendation
Replace the `payable(msg.sender).transfer(wad)` call in `WSEI.withdraw` with a low-level `call` pattern that forwards all available gas and checks the return value:
```solidity
(bool success, ) = msg.sender.call{value: wad}("");
require(success, "SEI_TRANSFER_FAILED");
```
Since the balance is already decremented before the external call (checks-effects-interactions is respected), this change is safe from reentrancy while removing the fixed-gas-stipend failure mode.

### Proof of Concept
1. Deploy a contract `Victim` whose `receive() external payable { someMapping[block.number] = msg.value; }` (a single cold `SSTORE`, costing ~20,000+ gas, far above 2300).
2. `Victim` calls `WSEI.deposit{value: 1 ether}()` — this succeeds, crediting `balanceOf[Victim]`.
3. `Victim` calls `WSEI.withdraw(1 ether)`.
4. `balanceOf[Victim]` is decremented, then `payable(Victim).transfer(1 ether)` attempts the SEI transfer, which runs `Victim.receive()` with only 2300 gas; the `SSTORE` inside `receive()` exceeds the stipend and reverts, unwinding the whole `withdraw` call.
5. `Victim` can never successfully call `withdraw` again for any amount — its wrapped SEI is permanently frozen in the WSEI contract.

### Citations

**File:** x/evm/artifacts/wsei/artifacts.go (L1-26)
```go
package wsei

import (
	"embed"
	"encoding/hex"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
)

const CurrentVersion uint16 = 1

//go:embed WSEI.abi
//go:embed WSEI.bin
var f embed.FS

var cachedBin []byte
var cachedABI *abi.ABI

func GetABI() []byte {
	bz, err := f.ReadFile("WSEI.abi")
	if err != nil {
		panic("failed to read WSEI contract ABI")
	}
	return bz
}
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
