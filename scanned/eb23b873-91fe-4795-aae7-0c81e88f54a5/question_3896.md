# Q3896: UpdateCommissionCollector checked-vs-unchecked auth split

## Question
Can an unprivileged attacker submit a transaction invoking vote-program `UpdateCommissionCollector` with authority fields, seeds, bls proof material, slot-hash timing, reward values, duplicated accounts, and same-transaction follow-up actions and drive `process_instruction` into a state where checked and unchecked authorization paths do not enforce equivalent invariants on the same logical authority change, violating the invariant that equivalent authorization variants must enforce equivalent safety conditions and leading to `Loss of Funds`?

## Target
- File/function: programs/vote/src/vote_processor.rs::process_instruction
- Entrypoint: submit a transaction invoking vote-program `UpdateCommissionCollector`
- Attacker controls: authority fields, seeds, BLS proof material, slot-hash timing, reward values, duplicated accounts, and same-transaction follow-up actions
- Exploit idea: diff semantically equivalent auth changes across variants
- Invariant to test: equivalent authorization variants must enforce equivalent safety conditions
- Expected Immunefi impact: Loss of Funds
- Fast validation: run paired auth changes through checked and unchecked variants
