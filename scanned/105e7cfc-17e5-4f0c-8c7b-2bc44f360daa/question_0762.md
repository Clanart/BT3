# Q762: Starknet fin_transfer state update before full validation

## Question
Can an unprivileged attacker exploit `public Starknet settlement entrypoint` so that `starknet/src/omni_bridge.cairo::fin_transfer` mutates finalization state before all signature or proof checks implied by checks pause flags, enforces `!is_transfer_finalised(destination_nonce)`, marks the nonce finalised, verifies the signed Borsh payload, and then releases native or bridge-token value are complete, violating `a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::fin_transfer`
- Entrypoint: `public Starknet settlement entrypoint`
- Attacker controls: signature fields, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: a signed inbound settlement must never be replayable, branch-switchable, or capable of failing after finalisation state changes in a way that strands or duplicates funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
