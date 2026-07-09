# Q2388: EVM OmniBridge native/bridged branching one inbound event spawns multiple outbound obligations through cross-module drift

## Question
Can an unprivileged attacker use `public EVM init/finalize entrypoints` with control over zero address versus token address, custom-minter registration, bridge-token registration, and message presence and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `one inbound event spawns multiple outbound obligations` attack class because selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` and the adjacent mint, burn, or custody accounting after every branch.
