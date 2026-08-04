# Q3269: Assign unauthorized signer bypass

## Question
Can an unprivileged attacker submit a transaction invoking system-program `Assign` with lamport amounts, seeds, owners, duplicated accounts, prefunded targets, nonce freshness, and multi-instruction ordering and drive `process_instruction` into a state where an unprivileged attacker can use duplicated account metas or role aliasing to satisfy a signer or authority check that should fail, violating the invariant that signer and authority checks must bind to the intended account, not only to an index shape and leading to `Loss of Funds`?

## Target
- File/function: programs/system/src/system_processor.rs::process_instruction
- Entrypoint: submit a transaction invoking system-program `Assign`
- Attacker controls: lamport amounts, seeds, owners, duplicated accounts, prefunded targets, nonce freshness, and multi-instruction ordering
- Exploit idea: probe whether positional or duplicated accounts can stand in for the real authority
- Invariant to test: signer and authority checks must bind to the intended account, not only to an index shape
- Expected Immunefi impact: Loss of Funds
- Fast validation: repeat authority and data accounts in different positions and trace which signer the processor trusts
