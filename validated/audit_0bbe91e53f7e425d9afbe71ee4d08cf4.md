Let me analyze the external bug pattern and search for analogs in nearcore.

The core bug in M-13: when a principal is set/updated via `setPrincipal`, a **zero/default value** is silently passed instead of the actual required value to an approval/binding function, causing the binding to be a no-op (no error, wrong state).

Let me search for nearcore analogs in chunk reconstruction, state sync, witness validation, and similar areas.