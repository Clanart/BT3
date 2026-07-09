# Q2084: EVM OmniBridge native/bridged branching one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `public EVM init/finalize entrypoints` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` both release local value and create a second valid outbound bridge obligation via selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
